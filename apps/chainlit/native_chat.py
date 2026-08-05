from __future__ import annotations

import csv
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS "User" (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  identifier TEXT UNIQUE NOT NULL,
  email TEXT UNIQUE,
  password_hash TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  "createdAt" TIMESTAMP NOT NULL DEFAULT NOW(),
  "updatedAt" TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Add columns for existing tables (idempotent migrations)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'User' AND column_name = 'email') THEN
    ALTER TABLE "User" ADD COLUMN email TEXT UNIQUE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'User' AND column_name = 'password_hash') THEN
    ALTER TABLE "User" ADD COLUMN password_hash TEXT;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS "Thread" (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT,
  "userId" UUID REFERENCES "User"(id) ON DELETE SET NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  tags TEXT,
  "createdAt" TIMESTAMP NOT NULL DEFAULT NOW(),
  "updatedAt" TIMESTAMP NOT NULL DEFAULT NOW(),
  "deletedAt" TIMESTAMP NULL
);

CREATE TABLE IF NOT EXISTS "Step" (
  id UUID PRIMARY KEY,
  "threadId" UUID REFERENCES "Thread"(id) ON DELETE CASCADE,
  "parentId" UUID NULL,
  input TEXT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  name TEXT NULL,
  output TEXT NULL,
  type TEXT NOT NULL,
  "startTime" TIMESTAMP NULL,
  "endTime" TIMESTAMP NULL,
  "showInput" TEXT NULL,
  "isError" BOOLEAN NOT NULL DEFAULT FALSE,
  "createdAt" TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS "Element" (
  id UUID PRIMARY KEY,
  "threadId" UUID REFERENCES "Thread"(id) ON DELETE CASCADE,
  "stepId" UUID REFERENCES "Step"(id) ON DELETE CASCADE,
  metadata TEXT NOT NULL DEFAULT '{}',
  mime TEXT NULL,
  name TEXT NULL,
  "objectKey" TEXT NULL,
  url TEXT NULL,
  "chainlitKey" TEXT NULL,
  display TEXT NULL,
  size BIGINT NULL,
  language TEXT NULL,
  page INTEGER NULL,
  "autoPlay" BOOLEAN NULL,
  "playerConfig" TEXT NULL,
  props TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS "Feedback" (
  id UUID PRIMARY KEY,
  "stepId" UUID REFERENCES "Step"(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  value DOUBLE PRECISION NULL,
  comment TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_thread_user_updated
  ON "Thread"("userId", "updatedAt" DESC);
CREATE INDEX IF NOT EXISTS idx_step_thread_start
  ON "Step"("threadId", "startTime");
CREATE INDEX IF NOT EXISTS idx_element_thread
  ON "Element"("threadId");
"""


async def ensure_native_schema(database_url: str) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(SCHEMA_SQL)
    finally:
        await conn.close()


async def create_user(
    database_url: str,
    username: str,
    email: str,
    password_hash: str,
) -> dict[str, Any] | None:
    """Create a new user with hashed password. Returns user dict or None if exists."""
    conn = await asyncpg.connect(database_url)
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO "User" (identifier, email, password_hash, metadata)
            VALUES ($1, $2, $3, '{"provider": "local"}')
            -- One untargeted ON CONFLICT, covering both the identifier and the
            -- email unique constraint. Postgres permits only a single ON CONFLICT
            -- clause per statement; two of them (one per constraint) is a syntax
            -- error, which made every registration fail with a 500.
            ON CONFLICT DO NOTHING
            RETURNING id, identifier, email, metadata, "createdAt"
            """,
            username,
            email,
            password_hash,
        )
        if row is None:
            return None
        return dict(row)
    finally:
        await conn.close()


async def get_user_by_identifier(
    database_url: str,
    identifier: str,
) -> dict[str, Any] | None:
    """Get user by username/identifier."""
    conn = await asyncpg.connect(database_url)
    try:
        row = await conn.fetchrow(
            """
            SELECT id, identifier, email, password_hash, metadata, "createdAt"
            FROM "User"
            WHERE identifier = $1
            """,
            identifier,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def get_user_by_email(
    database_url: str,
    email: str,
) -> dict[str, Any] | None:
    """Get user by email."""
    conn = await asyncpg.connect(database_url)
    try:
        row = await conn.fetchrow(
            """
            SELECT id, identifier, email, password_hash, metadata, "createdAt"
            FROM "User"
            WHERE email = $1
            """,
            email,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def check_user_exists(
    database_url: str,
    username: str | None = None,
    email: str | None = None,
) -> dict[str, bool]:
    """Check if username or email already exists."""
    conn = await asyncpg.connect(database_url)
    try:
        result = {"username_exists": False, "email_exists": False}
        if username:
            row = await conn.fetchrow(
                'SELECT 1 FROM "User" WHERE identifier = $1',
                username,
            )
            result["username_exists"] = row is not None
        if email:
            row = await conn.fetchrow(
                'SELECT 1 FROM "User" WHERE email = $1',
                email,
            )
            result["email_exists"] = row is not None
        return result
    finally:
        await conn.close()


async def upsert_feedback(
    database_url: str,
    *,
    feedback_id: str,
    step_id: str,
    value: float,
    comment: str | None = None,
) -> None:
    """Insert or update a feedback row in the Feedback table.

    Uses a unique constraint on ``stepId`` so that repeated clicks on the
    same step always update the existing row instead of creating duplicates.
    """
    import uuid as _uuid

    conn = await asyncpg.connect(database_url)
    try:
        # Ensure the unique index exists (idempotent).
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS "Feedback_stepId_unique"
            ON "Feedback" ("stepId")
            """
        )
        await conn.execute(
            """
            INSERT INTO "Feedback" (id, "stepId", name, value, comment)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT ("stepId") DO UPDATE
              SET value   = EXCLUDED.value,
                  comment = EXCLUDED.comment
            """,
            _uuid.UUID(feedback_id),
            _uuid.UUID(step_id),
            "user-feedback",
            value,
            comment,
        )
    finally:
        await conn.close()


async def export_all_chats_zip(
    *,
    database_url: str,
    out_dir: Path,
    user_id: str | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    jsonl_path = out_dir / f"native-chat-export-all-{stamp}.jsonl"
    csv_path = out_dir / f"native-chat-export-all-{stamp}.csv"
    zip_path = out_dir / f"native-chat-export-all-{stamp}.zip"

    conn = await asyncpg.connect(database_url)
    try:
        where_clause = 'WHERE t."deletedAt" IS NULL'
        params: list[Any] = []
        if user_id:
            where_clause += ' AND t."userId" = $1::uuid'
            params.append(user_id)

        threads = await conn.fetch(
            f"""
            SELECT
              t.id,
              t.name,
              t.metadata,
              t."createdAt",
              t."updatedAt",
              t."userId",
              u.identifier AS user_identifier
            FROM "Thread" t
            LEFT JOIN "User" u ON t."userId" = u.id
            {where_clause}
            ORDER BY t."updatedAt" DESC
            """,
            *params,
        )

        with jsonl_path.open("w", encoding="utf-8") as jf, csv_path.open(
            "w", encoding="utf-8", newline=""
        ) as cf:
            writer = csv.DictWriter(
                cf,
                fieldnames=[
                    "thread_id",
                    "thread_name",
                    "user_identifier",
                    "thread_created_at",
                    "thread_updated_at",
                    "step_id",
                    "step_type",
                    "step_name",
                    "step_start",
                    "step_end",
                    "step_input",
                    "step_output",
                    "step_is_error",
                ],
            )
            writer.writeheader()

            for thread in threads:
                steps = await conn.fetch(
                    """
                    SELECT id, type, name, input, output, "isError", "startTime", "endTime", metadata
                    FROM "Step"
                    WHERE "threadId" = $1::uuid
                    ORDER BY "startTime" NULLS LAST, "createdAt" NULLS LAST
                    """,
                    thread["id"],
                )
                payload = {
                    "thread": {
                        "id": str(thread["id"]),
                        "name": thread["name"],
                        "userId": str(thread["userId"]) if thread["userId"] else None,
                        "userIdentifier": thread["user_identifier"],
                        "metadata": json.loads(thread["metadata"] or "{}"),
                        "createdAt": thread["createdAt"].isoformat() if thread["createdAt"] else None,
                        "updatedAt": thread["updatedAt"].isoformat() if thread["updatedAt"] else None,
                    },
                    "steps": [
                        {
                            "id": str(step["id"]),
                            "type": step["type"],
                            "name": step["name"],
                            "input": step["input"],
                            "output": step["output"],
                            "isError": bool(step["isError"]),
                            "startTime": step["startTime"].isoformat() if step["startTime"] else None,
                            "endTime": step["endTime"].isoformat() if step["endTime"] else None,
                            "metadata": json.loads(step["metadata"] or "{}"),
                        }
                        for step in steps
                    ],
                }
                jf.write(json.dumps(payload, ensure_ascii=False))
                jf.write("\n")

                for step in steps:
                    writer.writerow(
                        {
                            "thread_id": str(thread["id"]),
                            "thread_name": thread["name"] or "",
                            "user_identifier": thread["user_identifier"] or "",
                            "thread_created_at": (
                                thread["createdAt"].isoformat() if thread["createdAt"] else ""
                            ),
                            "thread_updated_at": (
                                thread["updatedAt"].isoformat() if thread["updatedAt"] else ""
                            ),
                            "step_id": str(step["id"]),
                            "step_type": step["type"] or "",
                            "step_name": step["name"] or "",
                            "step_start": step["startTime"].isoformat() if step["startTime"] else "",
                            "step_end": step["endTime"].isoformat() if step["endTime"] else "",
                            "step_input": step["input"] or "",
                            "step_output": step["output"] or "",
                            "step_is_error": bool(step["isError"]),
                        }
                    )
    finally:
        await conn.close()

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(jsonl_path, arcname=jsonl_path.name)
        zf.write(csv_path, arcname=csv_path.name)
    return zip_path


async def export_feedback_csv(
    *,
    database_url: str,
    out_dir: Path,
) -> Path:
    """Export all feedback rows joined with question, answer and user as CSV."""
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"feedback-export-{_stamp()}.csv"

    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch(
            """
            SELECT
              u.identifier                  AS username,
              user_q.output                 AS user_question,
              COALESCE(child.output, s.output) AS assistant_answer,
              f.value                       AS feedback_value,
              f.comment                     AS feedback_comment,
              s."createdAt"                 AS answer_time,
              t.id                          AS thread_id,
              f.id                          AS feedback_id,
              f."stepId"                    AS step_id
            FROM "Feedback" f
            JOIN "Step" s   ON s.id = f."stepId"
            JOIN "Thread" t ON t.id = s."threadId"
            LEFT JOIN "User" u ON u.id = t."userId"
            LEFT JOIN LATERAL (
                SELECT cs.output
                FROM "Step" cs
                WHERE cs."parentId" = s.id
                  AND cs.type = 'assistant_message'
                ORDER BY cs."startTime" DESC
                LIMIT 1
            ) child ON true
            LEFT JOIN LATERAL (
                SELECT qs.output
                FROM "Step" qs
                WHERE qs."threadId" = s."threadId"
                  AND qs.type = 'user_message'
                  AND qs."startTime" < s."startTime"
                ORDER BY qs."startTime" DESC
                LIMIT 1
            ) user_q ON true
            ORDER BY s."createdAt" DESC
            """
        )

        fieldnames = [
            "username",
            "user_question",
            "assistant_answer",
            "feedback_value",
            "feedback_comment",
            "answer_time",
            "thread_id",
            "feedback_id",
            "step_id",
        ]
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "username": row["username"] or "",
                        "user_question": row["user_question"] or "",
                        "assistant_answer": row["assistant_answer"] or "",
                        "feedback_value": row["feedback_value"] if row["feedback_value"] is not None else "",
                        "feedback_comment": row["feedback_comment"] or "",
                        "answer_time": row["answer_time"].isoformat() if row["answer_time"] else "",
                        "thread_id": str(row["thread_id"]) if row["thread_id"] else "",
                        "feedback_id": str(row["feedback_id"]) if row["feedback_id"] else "",
                        "step_id": str(row["step_id"]) if row["step_id"] else "",
                    }
                )
    finally:
        await conn.close()

    return csv_path
