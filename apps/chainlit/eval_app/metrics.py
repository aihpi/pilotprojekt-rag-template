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


def _judge_and_embeddings(
    judge_model: str, embed_model: str, base_url: str | None, api_key: str | None
):
    from openai import AsyncOpenAI
    from ragas.embeddings import OpenAIEmbeddings
    from ragas.llms import llm_factory

    # Bare model names, no provider prefix: this speaks the OpenAI protocol to the
    # gateway directly, and the gateway rejects a prefixed name.
    client = AsyncOpenAI(
        base_url=openai_base_url(base_url) if base_url else None,
        api_key=api_key or "unused",
    )
    llm = llm_factory(judge_model, provider="openai", client=client)
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
        result = await AnswerRelevancy(llm=llm, embeddings=embeddings).ascore(
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
