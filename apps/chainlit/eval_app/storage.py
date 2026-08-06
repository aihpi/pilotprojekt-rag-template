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
    relevance        REAL
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
) -> str:
    row_id = str(uuid.uuid4())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO eval_scores (
                id, timestamp, message_id, thread_id, question, answer,
                contexts, config_signature, faithfulness, relevance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
