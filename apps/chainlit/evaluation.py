"""Answer-quality evaluation: config fingerprinting and the eval-service client.

Scoring itself does not happen here. It happens in the separate ``eval_app``
service — see ``config.schema.EvaluationConfig`` for why. This module is the thin
app-side half: it fingerprints the active configuration so scores can be grouped
by it, and it posts finished answers to the service.

Everything here fails silently. Evaluation is optional and off by default, so a
missing service, a slow judge or a malformed response must never surface to the
user or spoil an answer that was otherwise fine.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from config.schema import RagConfig

logger = logging.getLogger(__name__)

# Judge calls are two LLM round-trips plus an embedding, so the read budget is
# generous. The connect budget deliberately is not: the eval service being absent
# is the normal case (it is optional), and we would rather give up immediately
# than hold a background task open waiting for something that is not there.
_TIMEOUT = httpx.Timeout(120.0, connect=3.0)


def config_signature(cfg: "RagConfig") -> str:
    """Fingerprint the configuration an answer was produced under.

    Derived from the config object rather than the YAML file on purpose:
    ``config.loader`` folds the ``CHAT_MODEL`` / ``EMBED_MODEL`` /
    ``QDRANT_COLLECTION`` / ``CHUNK_MAX_CHARS`` env overrides into it, so this
    describes what actually ran — the only thing worth grouping scores by.

    ``collection`` is part of the signature because two configurations that differ
    only by collection would otherwise share one, silently pooling scores from
    different corpora.

    Caveat worth knowing when reading old rows: ``chunking.strategy`` and
    ``chunking.max_chars`` describe how the *collection was ingested*, not how
    this query was served. Re-ingesting the same collection with different
    chunking leaves historical rows describing a corpus that no longer exists.
    """
    # "|" cannot occur in a gateway model name, in a chunking strategy (a Literal)
    # or in a Qdrant collection name, so the parts stay unambiguously splittable.
    return "|".join(
        str(part)
        for part in (
            cfg.models.chat_model,
            cfg.models.embed_model,
            cfg.chunking.strategy,
            cfg.chunking.max_chars,
            cfg.vector_store.collection,
        )
    )


async def post_score(
    *,
    question: str,
    answer: str,
    contexts: list[str],
    thread_id: str | None = None,
    message_id: str | None = None,
    cfg: "RagConfig | None" = None,
) -> dict[str, Any] | None:
    """POST one finished answer to the eval service and return its scores.

    Returns ``None`` — and never raises — when evaluation is off, when there is
    nothing meaningful to score, or when the service cannot be reached.
    """
    if cfg is None:
        from config import get_config

        cfg = get_config()
    ev = cfg.evaluation
    if not ev.enabled:
        return None
    # Faithfulness asks whether the answer's claims are supported by the retrieved
    # chunks, which is unanswerable with no chunks. Scoring these anyway would
    # book a 0.0 against an answer that correctly said "not in the documents" and
    # drag every aggregate down with it.
    if not contexts or not answer.strip():
        return None

    payload = {
        "question": question,
        "answer": answer,
        "contexts": contexts,
        "metrics": list(ev.metrics),
        "judge_model": ev.judge_model or cfg.models.chat_model,
        "config_signature": config_signature(cfg),
        "thread_id": thread_id,
        "message_id": message_id,
    }
    url = f"{ev.service_url.rstrip('/')}/api/score"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        # Deliberately broad. Nothing this function can hit is worth failing an
        # answer over, and the user has no way to act on it either way.
        logger.warning("evaluation: scoring unavailable (%s)", exc)
        return None


def format_inline(scores: dict[str, Any] | None) -> str:
    """Render scores as the one-line panel shown under an answer.

    Returns ``""`` when there is nothing to show, so the caller can decide whether
    to attach an element by truthiness alone.
    """
    if not scores:
        return ""
    # The isinstance filter is what drops the sibling "<metric>_reason" strings.
    return " · ".join(
        f"{name.capitalize()}: {value * 100:.0f}%"
        for name, value in scores.items()
        if isinstance(value, (int, float))
    )
