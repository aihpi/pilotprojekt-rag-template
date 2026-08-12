"""SQLite store for scores, feedback, star ratings, gold conversations and benchmark jobs.

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
-- the assistant message id the scores are keyed by, so a rating cannot be joined to
-- its own score row. The dashboard groups by config_signature instead, which is
-- exact; see the ponytail note in the app's evaluation.post_feedback.
CREATE TABLE IF NOT EXISTS feedback (
    id               TEXT PRIMARY KEY,
    timestamp        TEXT NOT NULL,
    step_id          TEXT,
    thread_id        TEXT,
    config_signature TEXT,
    rating           TEXT NOT NULL CHECK(rating IN ('up', 'down')),
    failure_reason   TEXT,
    failure_category TEXT
);

CREATE INDEX IF NOT EXISTS idx_feedback_signature
ON feedback(config_signature);

-- A gold conversation: ordered user/assistant turns, frozen at marking time.
-- Deliberately NOT a foreign key into eval_scores: the reference answers must not
-- change when the corpus is re-ingested or a score row is cleaned up.
CREATE TABLE IF NOT EXISTS gold_answers (
    id               TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    thread_id        TEXT,
    message_id       TEXT UNIQUE,          -- marking the same answer twice stays one row
    turns            TEXT NOT NULL,        -- JSON [{"user": q, "assistant": a}, ...]
    config_signature TEXT NOT NULL,        -- what produced the reference answers
    active           INTEGER NOT NULL DEFAULT 1
);

-- 1-5 stars, in their own table: feedback.rating carries CHECK('up','down'), which
-- SQLite cannot widen without a table rebuild. message_id here is exact (it comes
-- from an action payload carrying the assistant message id), unlike feedback.step_id.
CREATE TABLE IF NOT EXISTS ratings (
    id               TEXT PRIMARY KEY,
    timestamp        TEXT NOT NULL,
    message_id       TEXT,
    thread_id        TEXT,
    config_signature TEXT,
    stars            INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS idx_ratings_signature ON ratings(config_signature);

-- Benchmark jobs: written by the dashboard's play button, claimed by the app's
-- poller. The queue exists because this service cannot answer questions (no
-- retrieval stack in the eval image) and the app takes no inbound calls from here —
-- polling keeps the dependency edge one-way.
-- ponytail: single-worker claiming with no lease/heartbeat; a job whose app dies
-- mid-run stays 'running' forever. Add a lease timestamp if that ever bites.
CREATE TABLE IF NOT EXISTS benchmark_jobs (
    id               TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    chat_model       TEXT NOT NULL,
    judge_model      TEXT,
    run_label        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK(status IN ('pending','running','done','error')),
    done_turns       INTEGER NOT NULL DEFAULT 0,
    total_turns      INTEGER NOT NULL DEFAULT 0,
    error            TEXT
);
"""


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
        for column, ddl in (
            ("detail", "TEXT"),
            # Replay provenance. `source` gets a constant default (ALTER-legal), and
            # pre-migration rows correctly become 'live' — that is what they were.
            ("source", "TEXT NOT NULL DEFAULT 'live'"),
            ("run_label", "TEXT"),
            ("gold_id", "TEXT"),
            ("gold_turn", "INTEGER"),
            ("similarity", "REAL"),
            # Who judged this row. Without it, changing the judge makes history
            # ambiguous: two rows with different scores could be one answer judged
            # by two different models. NULL on pre-migration rows means "unknown".
            ("judge_model", "TEXT"),
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE eval_scores ADD COLUMN {column} {ddl}")


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
    similarity: float | None = None,
    source: str = "live",
    run_label: str | None = None,
    gold_id: str | None = None,
    gold_turn: int | None = None,
    judge_model: str | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO eval_scores (
                id, timestamp, message_id, thread_id, question, answer,
                contexts, config_signature, faithfulness, relevance, detail,
                similarity, source, run_label, gold_id, gold_turn, judge_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
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
                similarity,
                source,
                run_label,
                gold_id,
                gold_turn,
                judge_model,
            ),
        )


def add_feedback(
    db_path: Path,
    *,
    rating: str,
    step_id: str | None = None,
    thread_id: str | None = None,
    config_signature: str | None = None,
    failure_reason: str | None = None,
    failure_category: str | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO feedback (
                id, timestamp, step_id, thread_id,
                config_signature, rating, failure_reason, failure_category
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                _utc_now_iso(),
                step_id,
                thread_id,
                config_signature,
                rating,
                failure_reason,
                failure_category,
            ),
        )


def stats_by_config(db_path: Path) -> list[dict[str, Any]]:
    """Per-signature aggregates for the dashboard, live answers only.

    ``AVG`` ignores NULLs, so a signature scored with only one metric enabled
    reports that metric and leaves the other null rather than reporting a zero.
    Thumbs, stars and score rows are counted in separate subqueries because a
    signature can have feedback with no scores (evaluation switched on later) or
    scores with no feedback (nobody clicked), and a join would silently drop
    either case.

    Replay rows are excluded: a benchmark re-asks the same questions many times,
    and folding those into the live table would let one click of the play button
    rewrite what real usage looked like. They aggregate in
    :func:`benchmark_stats` instead.
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
                    AND f.rating = 'down')                      AS thumbs_down,
                (SELECT AVG(r.stars) FROM ratings r
                  WHERE r.config_signature = s.config_signature) AS stars,
                (SELECT COUNT(*) FROM ratings r
                  WHERE r.config_signature = s.config_signature) AS stars_n
            FROM eval_scores s
            WHERE s.source = 'live'
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

        # Which answer the newest score belongs to, and whether that answer is
        # already a gold reference — the two facts the badge's gold suggestion
        # needs: the id keys the save request (and the browser's dismissals), the
        # flag keeps an accepted suggestion from ever nagging again.
        newest = conn.execute(
            """
            SELECT message_id,
                   EXISTS(SELECT 1 FROM gold_answers g
                          WHERE g.message_id = eval_scores.message_id
                            AND g.active = 1)  AS is_gold
            FROM eval_scores
            WHERE thread_id = ?
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
        "last_message_id": newest["message_id"] if newest else None,
        "last_message_gold": bool(newest["is_gold"]) if newest else False,
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


# --------------------------------------------------------------------------- #
# Star ratings
# --------------------------------------------------------------------------- #


def add_rating(
    db_path: Path,
    *,
    stars: int,
    message_id: str | None = None,
    thread_id: str | None = None,
    config_signature: str | None = None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ratings (id, timestamp, message_id, thread_id,
                                 config_signature, stars)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), _utc_now_iso(), message_id, thread_id,
             config_signature, stars),
        )


# --------------------------------------------------------------------------- #
# Gold conversations
# --------------------------------------------------------------------------- #


def add_gold(
    db_path: Path,
    *,
    turns: list[dict[str, str]],
    config_signature: str,
    thread_id: str | None = None,
    message_id: str | None = None,
) -> str | None:
    """Freeze a conversation as a gold reference. Returns its id.

    ``INSERT OR IGNORE`` on the unique ``message_id``: clicking "save as gold"
    twice on the same answer must not create a second reference that would then
    be replayed (and counted) twice. Returns the existing row's id in that case,
    so the caller cannot tell the difference — which is the right amount of
    information for an idempotent action.

    When a score row exists for ``message_id``, its ``config_signature`` wins over
    the posted one: the score row recorded the model that actually answered, while
    a caller with no session context can only offer the configured default.
    """
    gold_id = str(uuid.uuid4())
    with connect(db_path) as conn:
        if message_id is not None:
            scored = conn.execute(
                "SELECT config_signature FROM eval_scores WHERE message_id = ?"
                " ORDER BY timestamp DESC LIMIT 1",
                (message_id,),
            ).fetchone()
            if scored:
                config_signature = scored["config_signature"]
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO gold_answers
                (id, created_at, thread_id, message_id, turns, config_signature)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (gold_id, _utc_now_iso(), thread_id, message_id,
             json.dumps(turns, ensure_ascii=False), config_signature),
        )
        if cursor.rowcount == 0 and message_id is not None:
            row = conn.execute(
                "SELECT id FROM gold_answers WHERE message_id = ?", (message_id,)
            ).fetchone()
            return row["id"] if row else None
    return gold_id


def list_gold(db_path: Path, *, active_only: bool = True) -> list[dict[str, Any]]:
    where = "WHERE active = 1" if active_only else ""
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, thread_id, message_id, turns,
                   config_signature, active
            FROM gold_answers {where}
            ORDER BY created_at
            """
        ).fetchall()
    out = []
    for row in rows:
        entry = dict(row)
        try:
            entry["turns"] = json.loads(entry["turns"])
        except json.JSONDecodeError:
            entry["turns"] = []
        out.append(entry)
    return out


# --------------------------------------------------------------------------- #
# Benchmark jobs and results
# --------------------------------------------------------------------------- #


def create_job(
    db_path: Path,
    *,
    chat_model: str,
    run_label: str,
    judge_model: str | None = None,
) -> str:
    job_id = str(uuid.uuid4())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO benchmark_jobs (id, created_at, chat_model, judge_model, run_label)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, _utc_now_iso(), chat_model, judge_model, run_label),
        )
    return job_id


def claim_pending_job(db_path: Path) -> dict[str, Any] | None:
    """Atomically take the oldest pending job, or ``None``.

    The UPDATE with the nested SELECT is one statement, so two pollers cannot
    claim the same job — not that there should ever be two (see the schema
    comment), but the single-statement form costs nothing.
    """
    with connect(db_path) as conn:
        row = conn.execute(
            """
            UPDATE benchmark_jobs SET status = 'running'
            WHERE id = (SELECT id FROM benchmark_jobs WHERE status = 'pending'
                        ORDER BY created_at LIMIT 1)
            RETURNING id, chat_model, judge_model, run_label
            """
        ).fetchone()
    return dict(row) if row else None


def update_job(
    db_path: Path,
    job_id: str,
    *,
    status: str | None = None,
    done_turns: int | None = None,
    total_turns: int | None = None,
    error: str | None = None,
) -> None:
    sets, params = [], []
    for column, value in (
        ("status", status), ("done_turns", done_turns),
        ("total_turns", total_turns), ("error", error),
    ):
        if value is not None:
            sets.append(f"{column} = ?")
            params.append(value)
    if not sets:
        return
    with connect(db_path) as conn:
        conn.execute(
            f"UPDATE benchmark_jobs SET {', '.join(sets)} WHERE id = ?",
            (*params, job_id),
        )


def list_jobs(db_path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, chat_model, judge_model, run_label,
                   status, done_turns, total_turns, error
            FROM benchmark_jobs ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def benchmark_stats(db_path: Path) -> dict[str, Any]:
    """Replay results grouped per run, plus the gold total for coverage.

    ``n`` against ``gold_turns_total`` is the tell for a stale gold set: a
    re-ingested corpus can make gold questions unanswerable, and a run that
    covers 9/15 turns says so without anyone having to notice missing rows.
    """
    with connect(db_path) as conn:
        gold_turns_total = conn.execute(
            "SELECT COALESCE(SUM(json_array_length(turns)), 0) AS n"
            " FROM gold_answers WHERE active = 1"
        ).fetchone()["n"]
        rows = conn.execute(
            """
            SELECT run_label, config_signature,
                   COUNT(*)          AS n,
                   AVG(similarity)   AS similarity,
                   AVG(faithfulness) AS faithfulness,
                   AVG(relevance)    AS relevance
            FROM eval_scores
            WHERE source = 'replay'
            GROUP BY run_label, config_signature
            ORDER BY MAX(timestamp) DESC
            """
        ).fetchall()
    return {"gold_turns_total": gold_turns_total, "runs": [dict(row) for row in rows]}
