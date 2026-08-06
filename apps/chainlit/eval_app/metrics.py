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
import re
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

# How many per-claim verdict calls may be in flight at once. Bounded so a long answer
# with dozens of claims does not open dozens of simultaneous requests at a shared
# gateway; eight covers the typical answer without batching.
_VERDICT_CONCURRENCY = 8

# How many chunks each claim is checked against when there are more than this. Four was
# measured: at two, one claim flipped from supported to unsupported, and at four and
# eight the verdicts matched full-context checking exactly, including on an answer with
# three invented claims among three real ones.
_ROUTED_CHUNKS = 4

# Words long enough to carry meaning. Short ones ("der", "und", "was") match everything
# and would make the ranking noise. Includes German umlauts, since the corpus and the
# answers are German.
_WORD = re.compile(r"[a-zA-ZäöüÄÖÜß0-9]{4,}")


def route_contexts(
    claims: list[str], contexts: list[str], budget: int = _ROUTED_CHUNKS
) -> list[str]:
    """Pick the chunks each claim is worth checking against; one string per claim.

    Checking every claim against every chunk is what a ``fetch_document`` answer makes
    ruinous: measured on a real answer with 63 chunks and 12 claims, sending the whole
    71 kB context with each claim cost 226,594 input tokens and 75.4s — slower and far
    more expensive than not splitting at all. Routing first brought the same answer to
    40,181 tokens and 12.8s, against 39.3s for RAGAS's single batched call.

    Ranking is word overlap, not embeddings, because it is free: 0.003s and no API call,
    where embedding 63 chunks plus 12 claims took 11.4s and would have been half the
    remaining time. Ties break toward the longer chunk, which is likelier to contain a
    given detail.

    ponytail: a lexical heuristic, so a claim paraphrased entirely in different words
    could be routed away from the chunk that supports it and be marked unsupported —
    a false negative, the dangerous direction for this metric. The ``budget`` of four is
    the slack that makes that unlikely, and it was checked against full-context
    verdicts on a mixed answer with no disagreement either way. If it ever does drift,
    the upgrade is embedding the claims and chunks (accurate, ~11s) or passing the
    vectors retrieval already computed through the score request (accurate and free,
    but real plumbing).
    """
    if len(contexts) <= budget:
        # Nothing to gain from choosing: every claim sees everything.
        return ["\n".join(contexts)] * len(claims)

    # Tokenised once, not once per claim: 12 claims over 63 chunks would otherwise be
    # 756 re-tokenisations of up to 4 kB each.
    chunk_words = [set(_WORD.findall(c.lower())) for c in contexts]

    routed: list[str] = []
    for claim in claims:
        words = set(_WORD.findall(claim.lower()))
        ranked = sorted(
            range(len(contexts)),
            key=lambda i: (-len(words & chunk_words[i]), -len(contexts[i])),
        )[:budget]
        # Original order, so the numbering a judge sees follows the retrieval order.
        routed.append("\n".join(contexts[i] for i in sorted(ranked)))
    return routed


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

    from ragas.metrics.collections.faithfulness.util import NLIStatementOutput

    metric = Faithfulness(llm=llm)
    statements = await metric._create_statements(question, answer)
    if not statements:
        # RAGAS returns NaN here. Nothing to report and nothing to average.
        return {}

    # Each claim is checked against the chunks most likely to bear on it, rather than
    # against all of them. Without this, splitting is a pessimisation on any answer
    # built from fetch_document — see route_contexts for the measurements.
    routed = route_contexts(list(statements), contexts)

    # One call per claim, concurrently, instead of one call carrying all of them.
    #
    # RAGAS batches every claim into a single verdict call, and that call is the whole
    # critical path: it must generate a verdict *and* a written reason for each claim,
    # so its cost is output length. Measured on a real 8-claim answer with 6.2 kB of
    # context: 17.0s batched against 5.5s split.
    #
    # The context is resent with every call and that is close to free — 1 claim with
    # the full 6.2 kB took 2.9s against 2.6s with 400 bytes, so prefill is not what
    # costs. It is 8x the input tokens, which on a self-hosted gateway is compute
    # rather than money.
    #
    # This is still RAGAS's prompt and RAGAS's scoring: _create_verdicts takes a list,
    # so a one-element list changes the batching and nothing else. Verified on a
    # deliberately mixed answer (4 of 7 claims supported) that batched and split return
    # identical verdicts claim by claim, twice in a row.
    limit = asyncio.Semaphore(_VERDICT_CONCURRENCY)

    async def verdict_for(statement: str, claim_context: str):
        async with limit:
            return await metric._create_verdicts([statement], claim_context)

    # No return_exceptions: dropping a claim that failed would shrink the denominator
    # and quietly overstate the score. Better to lose the metric for this answer, which
    # the caller already records as absent rather than as zero.
    results = await asyncio.gather(
        *(verdict_for(s, c) for s, c in zip(statements, routed))
    )

    judged = [r.statements[0] for r in results if r.statements]
    if len(judged) != len(statements):
        raise ValueError(
            f"judge returned {len(judged)} verdicts for {len(statements)} claims"
        )

    # Reassembled so the score is still RAGAS's _compute_score rather than arithmetic
    # of ours, and gather preserves order so the claims line up with the answer.
    merged = NLIStatementOutput(statements=judged)
    score = float(metric._compute_score(merged))

    claims = [
        {"text": s.statement, "ok": bool(s.verdict), "why": s.reason}
        for s in merged.statements
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
