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

# The read budget has to cover a judge model grading an answer: ~16s for an ordinary
# one, ~28s when fetch_document put a whole paper in the context. The service runs its
# metrics concurrently, so this is a ceiling on the slowest one, not on their sum. Set
# far above the measurement because nothing is waiting on it — the answer is already
# delivered — and a gateway having a slow minute should not lose the score.
# The connect budget deliberately is not generous. The eval service being absent is
# the normal case, since it is optional, and giving up at once beats holding a
# background task open for something that is not there.
_TIMEOUT = httpx.Timeout(300.0, connect=3.0)


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


def _resolve(cfg: "RagConfig | None"):
    if cfg is None:
        from config import get_config

        cfg = get_config()
    return cfg, cfg.evaluation


def trend_sign(mean: float | None, last: float | None, answers: int) -> int:
    """Which way the last answer went against the conversation's average.

    ``1`` up, ``-1`` down, ``0`` for "about the same" — a sign only, because the
    badge has room for an arrow and not a second number.

    Returns ``0`` below two answers: with one answer the last value *is* the mean,
    so any arrow would be noise dressed up as a signal. The dead band keeps a
    judge's rounding jitter from showing as a trend.
    """
    if answers < 2 or mean is None or last is None:
        return 0
    delta = last - mean
    if delta > 0.01:
        return 1
    return -1 if delta < -0.01 else 0


async def _post(ev, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST to the eval service, returning ``None`` on any failure whatsoever.

    Deliberately broad: nothing reachable from here is worth failing an answer or a
    thumbs-click over, and the user has no way to act on it either way.
    """
    url = f"{ev.service_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        logger.warning("evaluation: %s unavailable (%s)", path, exc)
        return None


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
    cfg, ev = _resolve(cfg)
    if not ev.enabled:
        return None
    # Faithfulness asks whether the answer's claims are supported by the retrieved
    # chunks, which is unanswerable with no chunks. Scoring these anyway would
    # book a 0.0 against an answer that correctly said "not in the documents" and
    # drag every aggregate down with it.
    if not contexts or not answer.strip():
        return None

    return await _post(
        ev,
        "/api/score",
        {
            "question": question,
            "answer": answer,
            "contexts": contexts,
            "metrics": list(ev.metrics),
            "judge_model": ev.judge_model or cfg.models.chat_model,
            "embed_model": cfg.models.embed_model,
            "config_signature": config_signature(cfg),
            "thread_id": thread_id,
            "message_id": message_id,
        },
    )


async def post_feedback(
    *,
    rating: str,
    step_id: str | None = None,
    thread_id: str | None = None,
    comment: str | None = None,
    cfg: "RagConfig | None" = None,
) -> None:
    """Record a thumbs click for the dashboard. Never raises.

    Separate from the Postgres feedback row the app already writes: that one is
    per-thread and belongs to Chainlit's own history, this one is per
    configuration and belongs to the eval store. It is also independent of
    ``DATABASE_URL``, so thumbs stay measurable on an instance running without
    Postgres.

    ponytail: identifies the rated answer only by Chainlit's ``forId``, which may
    be the parent run step rather than the assistant message our scores are keyed
    by — so a feedback row cannot reliably be joined to its own score row. Nothing
    needs that yet (the dashboard groups by signature, which is exact), and the
    join would cost a Postgres round-trip per click. Resolve ``forId`` through the
    LEFT JOIN LATERAL pattern in ``native_chat.export_feedback_csv`` if a
    per-answer view is ever wanted.
    """
    cfg, ev = _resolve(cfg)
    if not ev.enabled:
        return
    await _post(
        ev,
        "/api/feedback",
        {
            "rating": rating,
            "step_id": step_id,
            "thread_id": thread_id,
            "comment": comment,
            "config_signature": config_signature(cfg),
            "judge_model": ev.judge_model or cfg.models.chat_model,
        },
    )
