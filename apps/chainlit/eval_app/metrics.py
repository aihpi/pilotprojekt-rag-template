"""Metric computation. The metric library lives behind exactly one function.

``score()`` is the entire public surface, so swapping the library underneath is a
change to one file. That has already been used once, replacing DeepEval with RAGAS
after DeepEval turned out unable to score against a self-hosted gateway at all.

The two live metrics are reference-free — no ground-truth answer needed — so they
work on real conversations:

* **faithfulness** — are the answer's claims supported by the retrieved chunks?
* **relevance** — does the answer address the question? Needs the embedding model as
  well as the judge.

**similarity** is the exception: embedding cosine against a known-good reference
answer. It only runs on benchmark replays, where a gold answer exists to compare
against, which is why it is not in ``SUPPORTED_METRICS`` (the live default).

Judge calls go to the same gateway the app uses, at ``temperature=0``: these numbers
are only meaningful as deltas, and a sampling judge adds noise that swamps the signal.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Literal

logger = logging.getLogger(__name__)

MetricName = Literal["faithfulness", "relevance", "similarity"]
# The live-path default. similarity is deliberately absent: it needs a reference
# answer, which only benchmark replays have — those name it explicitly.
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


# Chunks each claim is checked against when there are more than this. Measured: at two,
# a supported claim flipped to unsupported; at four and eight the verdicts matched
# full-context checking exactly.
_ROUTED_CHUNKS = 4

# Words long enough to carry meaning. Short ones ("der", "und", "was") match everything
# and would make the ranking noise. Includes German umlauts, since the corpus and the
# answers are German.
_WORD = re.compile(r"[a-zA-ZäöüÄÖÜß0-9]{4,}")


def route_contexts(
    claims: list[str], contexts: list[str], budget: int = _ROUTED_CHUNKS
) -> list[str]:
    """Pick the chunks each claim is worth checking against; one string per claim.

    Without this, checking claims individually is a pessimisation on any answer built
    from ``fetch_document``, which can retrieve a whole paper: see the table in
    docs/evaluation.md. Ranking is word overlap rather than embeddings because it is
    free, where embedding every chunk would cost more than the split saves.

    ponytail: lexical, so a claim paraphrased entirely in different words could be
    routed away from the chunk that supports it and marked unsupported — a false
    negative, the dangerous direction here. ``budget`` is the slack against that, and
    four was verified against full-context verdicts. Upgrade path is embeddings, or
    passing the vectors retrieval already computed through the score request.
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

    Everything RAGAS sends goes through instructor, which measured ~4s per call against
    ~1.5s for the client's own ``completions.parse`` on the same prompt and schema.
    The metrics only ever call ``agenerate``, so implementing that one method keeps
    their claim decomposition, prompts and scoring while dropping the slow layer.
    Subclassed rather than duck-typed because ``BaseMetric`` runs an ``isinstance``
    check.
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
                # One verdict *and* a reason per claim, so a detailed answer needs
                # room. Too small and the parse fails on a truncated response, which
                # dropped the metric silently.
                max_tokens=4096,
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
    reference: str | None = None,
) -> dict[str, Any]:
    """Run a single metric. Returns ``{}`` if it failed rather than raising.

    One metric failing must not cost the others: a judge that chokes on one
    question usually still grades the next, and a partial row is far more useful
    than no row. A failure is recorded as absent, never as 0.0 — which matters
    especially here, because 0.0 is itself a meaningful faithfulness score.
    """
    try:
        if name == "faithfulness":
            return await _faithfulness(question, answer, contexts, llm)
        if name == "similarity":
            # Requested without a reference to compare against: absent, not 0.0 —
            # the same rule as a failed metric, because "could not measure" and
            # "measured as totally dissimilar" are different claims. Guard before
            # the import, so the guard is testable where ragas is not installed.
            if not reference:
                return {}
            from ragas.metrics.collections import SemanticSimilarity

            result = await SemanticSimilarity(embeddings=embeddings).ascore(
                reference=reference, response=answer
            )
            return {"similarity": float(result.value)}
        from ragas.metrics.collections import AnswerRelevancy

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

    # One request per claim, concurrently, each against only the chunks routed to it.
    # RAGAS batches all claims into one verdict call, and that call is the critical
    # path because it generates a verdict *and* a written reason for every claim.
    # Splitting is ~3x faster; routing is what keeps it from costing 8x the tokens.
    # Still RAGAS's prompt either way — _create_verdicts takes a list, so a
    # one-element list changes the batching and nothing else.
    routed = route_contexts(list(statements), contexts)
    # Bounded so an answer with dozens of claims does not open dozens of requests.
    limit = asyncio.Semaphore(8)

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
    reference: str | None = None,
) -> dict[str, Any]:
    """Grade one answer. Returns ``{metric: float, metric_reason: str}``.

    ``reference`` is a known-good answer to the same question; only the
    ``similarity`` metric reads it.

    Metrics run concurrently, so the wall clock is the slowest metric rather than
    the sum. Natively async, with no worker threads: RAGAS is async throughout, and
    driving an async library from a thread with no event loop is what made the
    previous implementation hang.
    """
    llm, embeddings = _judge_and_embeddings(judge_model, embed_model, base_url, api_key)
    parts = await asyncio.gather(
        *(
            _score_one(name, question, answer, contexts, llm, embeddings, reference)
            for name in metrics
        )
    )
    return {key: value for part in parts for key, value in part.items()}
