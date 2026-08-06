"""SQLite store for evaluation scores, feedback and A/B comparisons.

This service is the *only* writer. The Chainlit app posts finished answers here
and never touches the file — which is deliberate: the one SQLite corruption this
project has actually suffered came from concurrent access to a WAL database on a
shared mount, so there is exactly one process holding the pen.

Connection handling mirrors ``chat_history.py`` (WAL, row factory, idempotent
``CREATE TABLE IF NOT EXISTS`` on every open) so the two stores behave the same
way for anyone reading both.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS eval_scores (
    id               TEXT PRIMARY KEY,
    timestamp        TEXT NOT NULL,
    message_id       TEXT,
    thread_id        TEXT,
    question         TEXT NOT NULL,
    answer           TEXT NOT NULL,
    contexts         TEXT NOT NULL DEFAULT '[]',
    config_signature TEXT NOT NULL,
    faithfulness     REAL,
    relevance        REAL,
    -- Why the judge scored it that way: the per-claim verdicts behind faithfulness,
    -- and whether the answer declined to answer (which forces relevance to 0). JSON
    -- rather than columns because it is read whole and never queried into.
    detail           TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_scores_signature
ON eval_scores(config_signature);

-- Feedback arrives keyed by the Chainlit step id, which is not the same thing as
-- the assistant message id the scores are keyed by; both are recorded so the
-- dashboard can join either way.
CREATE TABLE IF NOT EXISTS feedback (
    id               TEXT PRIMARY KEY,
    timestamp        TEXT NOT NULL,
    message_id       TEXT,
    step_id          TEXT,
    thread_id        TEXT,
    config_signature TEXT,
    rating           TEXT NOT NULL CHECK(rating IN ('up', 'down')),
    failure_reason   TEXT,
    failure_category TEXT
);

CREATE INDEX IF NOT EXISTS idx_feedback_signature
ON feedback(config_signature);
"""
# No `comparisons` table yet — A/B has no writer. This script re-runs on every
# open and every statement is IF NOT EXISTS, so adding one later needs no
# migration, which is why there is nothing to gain by declaring it early.


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        # CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
        # a column added after someone has scored answers needs an explicit ALTER.
        # Same pattern as chat_history's column migrations.
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(eval_scores)")}
        if "detail" not in existing:
            conn.execute("ALTER TABLE eval_scores ADD COLUMN detail TEXT")


def add_score(
    db_path: Path,
    *,
    question: str,
    answer: str,
    contexts: list[str],
    config_signature: str,
    faithfulness: float | None = None,
    relevance: float | None = None,
    message_id: str | None = None,
    thread_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> str:
    row_id = str(uuid.uuid4())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO eval_scores (
                id, timestamp, message_id, thread_id, question, answer,
                contexts, config_signature, faithfulness, relevance, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                _utc_now_iso(),
                message_id,
                thread_id,
                question,
                answer,
                json.dumps(contexts, ensure_ascii=False),
                config_signature,
                faithfulness,
                relevance,
                json.dumps(detail, ensure_ascii=False) if detail else None,
            ),
        )
    return row_id


def add_feedback(
    db_path: Path,
    *,
    rating: str,
    message_id: str | None = None,
    step_id: str | None = None,
    thread_id: str | None = None,
    config_signature: str | None = None,
    failure_reason: str | None = None,
    failure_category: str | None = None,
) -> str:
    row_id = str(uuid.uuid4())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO feedback (
                id, timestamp, message_id, step_id, thread_id,
                config_signature, rating, failure_reason, failure_category
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                _utc_now_iso(),
                message_id,
                step_id,
                thread_id,
                config_signature,
                rating,
                failure_reason,
                failure_category,
            ),
        )
    return row_id


def stats_by_config(db_path: Path) -> list[dict[str, Any]]:
    """Per-signature aggregates for the dashboard.

    ``AVG`` ignores NULLs, so a signature scored with only one metric enabled
    reports that metric and leaves the other null rather than reporting a zero.
    Thumbs and score rows are counted in separate subqueries because a signature
    can have feedback with no scores (evaluation switched on later) or scores with
    no feedback (nobody clicked), and a join would silently drop either case.
    """
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                s.config_signature                              AS config_signature,
                COUNT(*)                                        AS answers,
                AVG(s.faithfulness)                             AS faithfulness,
                AVG(s.relevance)                                AS relevance,
                (SELECT COUNT(*) FROM feedback f
                  WHERE f.config_signature = s.config_signature
                    AND f.rating = 'up')                        AS thumbs_up,
                (SELECT COUNT(*) FROM feedback f
                  WHERE f.config_signature = s.config_signature
                    AND f.rating = 'down')                      AS thumbs_down
            FROM eval_scores s
            GROUP BY s.config_signature
            ORDER BY s.config_signature
            """
        ).fetchall()
    return [dict(row) for row in rows]


def thread_summary(db_path: Path, thread_id: str) -> dict[str, Any]:
    """Running numbers for one conversation, for the badge above the chatbox.

    ``AVG`` ignores NULLs, so a conversation where some answers scored and others
    did not reports the mean of what actually scored, rather than being dragged
    towards zero by failures. ``answers`` counts every scored *attempt* though,
    including the ones that produced nothing — the badge shows it because "94% over
    2 answers" and "94% over 20" are different claims.

    ``last_*`` is the most recent non-null value, which is what the trend arrow
    compares against the mean. A partially scored conversation is the normal case,
    not an error, so every field is independently nullable.
    """
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)            AS answers,
                AVG(faithfulness)   AS faithfulness,
                AVG(relevance)      AS relevance
            FROM eval_scores
            WHERE thread_id = ?
            """,
            (thread_id,),
        ).fetchone()
        # Separate lookups rather than a window function: the newest row with a
        # faithfulness score is not necessarily the newest row with a relevance one.
        last = {}
        for metric in ("faithfulness", "relevance"):
            found = conn.execute(
                f"""
                SELECT {metric} FROM eval_scores
                WHERE thread_id = ? AND {metric} IS NOT NULL
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
            last[f"last_{metric}"] = found[0] if found else None

        # The newest row that actually explains itself. An older explanation is more
        # use than none, so this is not restricted to the very latest row.
        detail_row = conn.execute(
            """
            SELECT detail FROM eval_scores
            WHERE thread_id = ? AND detail IS NOT NULL
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """,
            (thread_id,),
        ).fetchone()

    detail = None
    if detail_row and detail_row["detail"]:
        try:
            detail = json.loads(detail_row["detail"])
        except json.JSONDecodeError:
            detail = None

    return {
        "thread_id": thread_id,
        "answers": row["answers"] or 0,
        "faithfulness": row["faithfulness"],
        "relevance": row["relevance"],
        **last,
        "last_detail": detail,
    }


def failure_categories(db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT config_signature, failure_category, COUNT(*) AS n
            FROM feedback
            WHERE rating = 'down' AND failure_category IS NOT NULL
            GROUP BY config_signature, failure_category
            ORDER BY config_signature, n DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]
