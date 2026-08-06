"""Metric computation. The metric library lives behind exactly one function.

``score()`` is the entire public surface of this module, so swapping DeepEval for
RAGAS (or anything else) is a change to one file with one call site. That is worth
the small indirection: DeepEval was chosen over RAGAS partly because RAGAS had gone
quiet upstream, and that judgement could age.

Both metrics are reference-free — they need no ground-truth answer — so they work
on real conversations:

* **faithfulness** — are the answer's claims supported by the retrieved chunks?
* **relevance** — does the answer address the question that was asked?

Judge calls go through LiteLLM to the same gateway the app uses, at
``temperature=0``. Determinism matters here: these numbers are only meaningful as
deltas between runs, and a sampling judge adds noise that swamps the signal you
are looking for.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Literal

# Set before DeepEval is imported, because it reads these at import time. Both
# posthog and sentry-sdk are hard dependencies of deepeval and phone home by
# default, which is not acceptable for a public-institution deployment. The compose
# file sets these too; doing it here as well covers `uv run` and any other entry
# point, so the opt-out cannot be lost by running the service a different way.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_UPDATE_WARNING_OPT_OUT", "YES")
os.environ.setdefault("ERROR_REPORTING", "NO")

logger = logging.getLogger(__name__)

MetricName = Literal["faithfulness", "relevance"]
SUPPORTED_METRICS: tuple[MetricName, ...] = ("faithfulness", "relevance")


def litellm_model_name(model: str) -> str:
    """Prefix a bare gateway model name with its LiteLLM provider.

    DeepEval's ``LiteLLMModel`` has no ``custom_llm_provider`` argument, and its
    docs require the provider in the model string, so a bare name like
    ``gpt-oss-120b`` would raise "LLM Provider NOT provided". LiteLLM strips the
    ``openai/`` prefix before it builds the request, so the gateway still receives
    the bare name it expects — the prefix is routing metadata, not part of the
    wire format.

    Mirrors the same decision in ``llm._client_args``: a model that already carries
    a provider (``anthropic/…``) keeps it.
    """
    return model if "/" in model else f"openai/{model}"


def _measure(
    question: str,
    answer: str,
    contexts: list[str],
    metrics: list[MetricName],
    judge: Any,
) -> dict[str, Any]:
    from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
    from deepeval.test_case import LLMTestCase

    case = LLMTestCase(
        input=question,
        actual_output=answer,
        retrieval_context=list(contexts),
    )
    builders = {
        "faithfulness": FaithfulnessMetric,
        "relevance": AnswerRelevancyMetric,
    }

    results: dict[str, Any] = {}
    for name in metrics:
        # Unknown names are rejected by the request schema, so a KeyError here
        # would be a programming error and should be loud.
        metric = builders[name](model=judge)
        try:
            metric.measure(case)
        except Exception as exc:
            # One metric failing must not cost the others. A judge that refuses
            # one answer will usually still grade the next, and a partial row is
            # far more useful than no row.
            logger.warning("evaluation: metric %s failed (%s)", name, exc)
            continue
        results[name] = metric.score
        if getattr(metric, "reason", None):
            results[f"{name}_reason"] = metric.reason
    return results


async def score(
    question: str,
    answer: str,
    contexts: list[str],
    *,
    metrics: list[MetricName],
    judge_model: str,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Grade one answer. Returns ``{metric: float, metric_reason: str}``.

    Metrics that fail are omitted rather than reported as zero — a judge error is
    not evidence of a bad answer, and a 0.0 would be indistinguishable from one in
    the aggregates.
    """
    from deepeval.models import LiteLLMModel

    judge = LiteLLMModel(
        model=litellm_model_name(judge_model),
        base_url=base_url,
        api_key=api_key,
        temperature=0.0,
    )
    # measure() is synchronous and makes several LLM round-trips. Off the event
    # loop it goes, so one slow judge cannot stall the whole service.
    return await asyncio.to_thread(_measure, question, answer, contexts, metrics, judge)
