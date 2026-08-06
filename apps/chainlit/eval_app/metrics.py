"""Metric computation. The metric library lives behind exactly one function.

``score()`` is the entire public surface, so the library underneath is a
one-file decision. That earned its keep immediately: this was written against
DeepEval first and swapped to RAGAS without touching a caller.

Why RAGAS, having initially picked DeepEval on dependency weight and release
activity: DeepEval could not actually score an answer against a self-hosted
OpenAI-compatible gateway. Faithfulness worked, but AnswerRelevancy hung inside
``litellm.acompletion`` on its *second* call and died on DeepEval's internal
88.5s per-attempt timeout, every time, on every model that was up. Traced it to
one successful call followed by one that never returns, which is a connection-reuse
hang in litellm's async client rather than anything about prompts or schemas: the
prompts were ~350 tokens and the same schemas answered in 1.6s called directly.
DeepEval's own timeout also ignored ``litellm.request_timeout``, so it could not be
bounded from outside either.

RAGAS takes an ``openai.AsyncOpenAI`` client directly, so it never goes through
litellm and the whole failure mode disappears. Both metrics score on the first try.

Both metrics are reference-free — they need no ground-truth answer — so they work
on real conversations:

* **faithfulness** — are the answer's claims supported by the retrieved chunks?
* **relevance** — does the answer address the question that was asked? Needs the
  embedding model as well as the judge: it generates questions from the answer and
  compares them to the real one.

Judge calls go to the same gateway the app uses, at ``temperature=0``. Determinism
matters: these numbers are only meaningful as deltas between runs, and a sampling
judge adds noise that swamps the signal.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

MetricName = Literal["faithfulness", "relevance"]
SUPPORTED_METRICS: tuple[MetricName, ...] = ("faithfulness", "relevance")


def openai_base_url(base_url: str) -> str:
    """Normalise a gateway URL to the ``/v1`` root the OpenAI client expects.

    The app's ``LITELLM_BASE_URL`` is the gateway root and may or may not carry a
    trailing slash or an explicit ``/v1`` (both forms appear in the wild, and the
    example env file ships the trailing-slash one). Appending blindly yields
    ``…//v1`` or ``…/v1/v1``, both of which 404.
    """
    trimmed = base_url.rstrip("/")
    return trimmed if trimmed.endswith("/v1") else f"{trimmed}/v1"


# Faithfulness returns one verdict *with a reason* per claim, so a long answer
# produces a long structured response. Against the gateway's default budget that
# truncates, the parse fails, and the metric is dropped with "The output is incomplete
# due to a max_tokens length limit" — silently, apart from that log line. The answers
# it hit were the detailed ones with many claims, exactly the ones worth checking.
_MAX_TOKENS = 4096


def _direct_judge(client, model: str):
    """RAGAS's prompts and scoring, with its transport replaced.

    RAGAS routes every call through instructor, and that wrapper costs about four
    seconds per call on top of the request. Measured against the same model, prompt
    and schema: ``llm.agenerate`` 5.5s, the client's own ``completions.parse`` 1.5s.
    At three calls per scored answer that was most of the wall clock — and none of it
    was the gateway, which answers a plain request in 0.6s, a structured one in 0.9s,
    and three concurrent structured ones in 1.2s.

    The two metrics only ever call ``agenerate`` (checked against their source), so
    implementing that single method keeps everything worth having from RAGAS — the
    claim decomposition, the prompts, the scoring — and drops the slow layer. The base
    class must be subclassed rather than duck-typed, because ``BaseMetric`` runs an
    ``isinstance`` check on it.
    """
    from ragas.llms.base import InstructorBaseRagasLLM

    class _DirectJudge(InstructorBaseRagasLLM):
        def generate(self, prompt, response_model):
            # These metrics are async throughout; nothing calls the sync path.
            raise NotImplementedError("use agenerate")

        async def agenerate(self, prompt, response_model):
            response = await client.chat.completions.parse(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format=response_model,
                # A sampling judge adds exactly the run-to-run noise the docs tell
                # people to read through.
                temperature=0.0,
                max_tokens=_MAX_TOKENS,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise ValueError(
                    f"judge returned nothing parsable as {response_model.__name__}"
                )
            return parsed

    return _DirectJudge()


def _judge_and_embeddings(
    judge_model: str, embed_model: str, base_url: str | None, api_key: str | None
):
    from openai import AsyncOpenAI
    from ragas.embeddings import OpenAIEmbeddings

    # Bare model names, no provider prefix: this speaks the OpenAI protocol to the
    # gateway directly, and the gateway rejects a prefixed name.
    client = AsyncOpenAI(
        base_url=openai_base_url(base_url) if base_url else None,
        api_key=api_key or "unused",
    )
    llm = _direct_judge(client, judge_model)
    embeddings = OpenAIEmbeddings(client=client, model=embed_model)
    return llm, embeddings


async def _score_one(
    name: MetricName,
    question: str,
    answer: str,
    contexts: list[str],
    llm: Any,
    embeddings: Any,
) -> dict[str, Any]:
    """Run a single metric. Returns ``{}`` if it failed rather than raising.

    One metric failing must not cost the others: a judge that chokes on one
    question usually still grades the next, and a partial row is far more useful
    than no row. A failure is recorded as absent, never as 0.0 — which matters
    especially here, because 0.0 is itself a meaningful faithfulness score.
    """
    from ragas.metrics.collections import AnswerRelevancy

    try:
        if name == "faithfulness":
            return await _faithfulness(question, answer, contexts, llm)
        # AnswerRelevancy takes no contexts by design: it asks whether the answer
        # fits the question, which is answerable without them.
        # strictness=1 rather than RAGAS's default 3. The parameter regenerates the
        # questions N times to average out judge variance, but RAGAS loops with an
        # await inside, so the calls are *serial*: measured 24.0s at 3 against 13.5s
        # at 1, for scores of 0.278 and 0.277. Paying eleven seconds for the third
        # decimal is not a trade worth making on a metric the docs tell you to read as
        # a delta. Raise it here if a noisy judge ever makes the averaging earn its
        # keep.
        result = await AnswerRelevancy(llm=llm, embeddings=embeddings, strictness=1).ascore(
            user_input=question, response=answer
        )
    except Exception as exc:
        logger.warning("evaluation: metric %s failed (%s)", name, exc)
        return {}

    score = float(result.value)
    out: dict[str, Any] = {"relevance": score}
    # RAGAS computes `cosine_sim.mean() * int(not all_noncommittal)`, so a score of
    # exactly 0 means the answer was judged noncommittal ("not in the documents")
    # and the similarity was thrown away — not that the answer was off-topic. Worth
    # surfacing, because a bare 0% reads as a failure when the assistant did the
    # right thing by declining.
    #
    # ponytail: inferred from the exact zero rather than read from the flag, because
    # RAGAS keeps that flag in a local inside ascore() with no accessor, and the
    # alternative is reproducing its numpy cosine block. A genuine mean cosine of
    # exactly 0.0 needs perfectly orthogonal embeddings, so the inference is safe.
    # Reproduce the loop if RAGAS ever exposes the flag.
    if score == 0.0:
        out["relevance_declined"] = True
    return out


async def _faithfulness(
    question: str, answer: str, contexts: list[str], llm: Any
) -> dict[str, Any]:
    """Faithfulness, keeping the per-claim verdicts that ``ascore()`` discards.

    ``Faithfulness.ascore()`` breaks the answer into atomic claims, checks each
    against the retrieved chunks, and then returns only the ratio — throwing away a
    per-claim verdict *with a reason*, which is the most useful thing it produced.
    Driving its three steps directly keeps them, and the score is still RAGAS's own
    ``_compute_score`` rather than arithmetic of ours.

    These are private methods. Acceptable here because ``ragas`` is pinned to an
    exact version, so they cannot shift underneath us, and because the alternative
    is showing a number with no way to see what it counted.
    """
    from ragas.metrics.collections import Faithfulness

    metric = Faithfulness(llm=llm)
    statements = await metric._create_statements(question, answer)
    if not statements:
        # RAGAS returns NaN here. Nothing to report and nothing to average.
        return {}
    verdicts = await metric._create_verdicts(statements, "\n".join(contexts))
    score = float(metric._compute_score(verdicts))

    claims = [
        {"text": s.statement, "ok": bool(s.verdict), "why": s.reason}
        for s in verdicts.statements
    ]
    return {"faithfulness": score, "faithfulness_claims": claims}


async def score(
    question: str,
    answer: str,
    contexts: list[str],
    *,
    metrics: list[MetricName],
    judge_model: str,
    embed_model: str,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Grade one answer. Returns ``{metric: float, metric_reason: str}``.

    Metrics run concurrently, so the wall clock is the slowest metric rather than
    the sum. Natively async, with no worker threads: RAGAS is async throughout, and
    driving an async library from a thread with no event loop is what made the
    previous implementation hang.
    """
    llm, embeddings = _judge_and_embeddings(judge_model, embed_model, base_url, api_key)
    parts = await asyncio.gather(
        *(
            _score_one(name, question, answer, contexts, llm, embeddings)
            for name in metrics
        )
    )
    return {key: value for part in parts for key, value in part.items()}
