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


def effective_chunking(cfg: "RagConfig") -> tuple[str, str]:
    """The chunking a collection was really ingested with, as ``(strategy, max_chars)``.

    Not ``cfg.chunking``, which is only the fallback: every data source may override
    it (``data_sources[].chunking``), and the shipped papers example does exactly
    that — it has no top-level ``chunking:`` block at all, so reading the global one
    returned the schema default ``fixed_size`` for a corpus ingested ``semantic``.

    Sources that disagree are reported as they are rather than resolved to one of
    them: several sources can feed one collection, and there is then no single true
    answer to give.
    """
    used = {
        ((s.chunking or cfg.chunking).strategy, (s.chunking or cfg.chunking).max_chars)
        for s in cfg.data_sources
    } or {(cfg.chunking.strategy, cfg.chunking.max_chars)}
    strategies = sorted({s for s, _ in used})
    sizes = sorted({str(m) for _, m in used})
    return "+".join(strategies), "+".join(sizes)


def config_signature(cfg: "RagConfig", chat_model: str | None = None) -> str:
    """Fingerprint the configuration an answer was produced under.

    Derived from the config object rather than the YAML file on purpose:
    ``config.loader`` folds the ``CHAT_MODEL`` / ``EMBED_MODEL`` /
    ``QDRANT_COLLECTION`` / ``CHUNK_MAX_CHARS`` env overrides into it, so this
    describes what actually ran — the only thing worth grouping scores by.

    ``chat_model`` is the model that actually answered, which is not always the
    configured one: the settings panel lets a user switch models per session, and
    that choice is persisted. Passing it is what keeps a Gemma answer from being
    filed under gpt-oss-120b. Falls back to the configured model for callers with
    no session to ask.

    ``collection`` is part of the signature because two configurations that differ
    only by collection would otherwise share one, silently pooling scores from
    different corpora.

    The retrieval mode is in for the same reason one level down: the same corpus
    searched dense and searched hybrid is a different retrieval path, and the
    resulting scores are not even on the same scale (cosine versus a fused rank),
    so pooling them would average incomparable numbers.

    It is encoded as one part describing what *actually ran* — ``dense``, or
    ``hybrid:<fusion>:<prefetch_limit>`` — rather than as one field per setting.
    ``fusion`` and ``prefetch_limit`` only mean anything when ``hybrid`` is on, so
    listing them unconditionally split two identical dense runs apart whenever the
    inert setting differed. And ``prefetch_limit`` does belong in the hybrid case:
    it decides which candidates fusion ever sees, so a wider pool can surface a
    chunk that neither leg ranked in its own top-k, changing the answer.

    Caveat worth knowing when reading old rows: the chunking fields describe how the
    *collection was ingested*, not how this query was served. Re-ingesting the same
    collection with different chunking leaves historical rows describing a corpus
    that no longer exists.
    """
    strategy, max_chars = effective_chunking(cfg)
    retrieval = (
        f"hybrid:{cfg.retrieval.fusion}:{cfg.retrieval.prefetch_limit}"
        if cfg.retrieval.hybrid
        else "dense"
    )
    # "|" cannot occur in a gateway model name, in a chunking strategy (a Literal)
    # or in a Qdrant collection name, so the parts stay unambiguously splittable.
    return "|".join(
        str(part)
        for part in (
            chat_model or cfg.models.chat_model,
            cfg.models.embed_model,
            strategy,
            max_chars,
            cfg.vector_store.collection,
            retrieval,
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
    chat_model: str | None = None,
    cfg: "RagConfig | None" = None,
) -> dict[str, Any] | None:
    """POST one finished answer to the eval service and return its scores.

    ``chat_model`` is the model that produced this answer, which the caller has to
    supply because it can differ per session — see :func:`config_signature`.

    Returns ``None`` — and never raises — when evaluation is off, when there is
    nothing meaningful to score, or when the service cannot be reached.
    """
    cfg, ev = _resolve(cfg)
    if not ev.enabled:
        return None
    if not answer.strip():
        return None
    # Faithfulness is unanswerable without chunks, but relevance only needs the
    # question and the answer. Skip only when every requested metric needs chunks.
    if not contexts and "faithfulness" in ev.metrics and "relevance" not in ev.metrics:
        return None
    metrics = list(ev.metrics)
    if not contexts:
        metrics = [m for m in metrics if m != "faithfulness"]
    if not metrics:
        return None

    return await _post(
        ev,
        "/api/score",
        {
            "question": question,
            "answer": answer,
            "contexts": contexts,
            "metrics": metrics,
            # `judge_model: null` is documented as "the chat model", so it follows
            # the one that actually answered rather than the configured default.
            "judge_model": ev.judge_model or chat_model or cfg.models.chat_model,
            "embed_model": cfg.models.embed_model,
            "config_signature": config_signature(cfg, chat_model),
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
    chat_model: str | None = None,
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
            "config_signature": config_signature(cfg, chat_model),
            "judge_model": ev.judge_model or chat_model or cfg.models.chat_model,
        },
    )


def conversation_turns(
    messages: list[dict[str, Any]], turn_index: int | None = None
) -> list[dict[str, str]]:
    """A message history as completed ``{"user", "assistant"}`` pairs.

    Tool and system messages fall out; an assistant message only counts once a
    user question precedes it (the paired shape is what a benchmark replays), and
    a trailing unanswered user turn is dropped. ``turn_index`` truncates to the
    first N pairs.

    Works on both message shapes this app stores: the in-session OpenAI list and
    the rows ``chat_history.get_session_messages`` returns.
    """
    turns: list[dict[str, str]] = []
    pending_user: str | None = None
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "user":
            pending_user = content
        elif role == "assistant" and pending_user is not None:
            turns.append({"user": pending_user, "assistant": content})
            pending_user = None
    if turn_index is not None:
        turns = turns[: max(0, turn_index)]
    return turns


def gold_suggested(summary: dict[str, Any], ev) -> bool:
    """Whether the badge should offer to save this conversation as gold.

    The judge scouts, the human decides: the newest scored answer clearing both
    thresholds makes the badge grow its marker, and the save action sits in the
    panel. Never suggested when either threshold is off (``null``), when the
    metric is missing (a failed judge is not evidence of quality), or when this
    answer is already gold (re-asking would nag).
    """
    if ev.gold_min_faithfulness is None or ev.gold_min_relevance is None:
        return False
    if summary.get("last_message_gold"):
        return False
    faithfulness = summary.get("last_faithfulness")
    relevance = summary.get("last_relevance")
    if faithfulness is None or relevance is None:
        return False
    return faithfulness >= ev.gold_min_faithfulness and relevance >= ev.gold_min_relevance


async def post_gold(
    *,
    turns: list[dict[str, str]],
    message_id: str | None = None,
    thread_id: str | None = None,
    chat_model: str | None = None,
    cfg: "RagConfig | None" = None,
) -> dict[str, Any] | None:
    """Freeze a conversation as a gold reference. Returns the service reply.

    ``None`` means the service was unreachable (or evaluation is off) — the
    caller should say so, because unlike a lost score a lost gold marking is a
    user action that silently failing would betray.
    """
    cfg, ev = _resolve(cfg)
    if not ev.enabled or not turns:
        return None
    return await _post(
        ev,
        "/api/gold",
        {
            "turns": turns,
            "message_id": message_id,
            "thread_id": thread_id,
            "config_signature": config_signature(cfg, chat_model),
        },
    )
