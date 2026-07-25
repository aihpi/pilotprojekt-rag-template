# Feedback persistence & CSV export

## Overview

Chainlit renders feedback buttons in the chat UI (Helpful / Not helpful plus an
optional free-text comment). Without a registered `@cl.on_feedback` handler,
however, those clicks are **not** written to the database.

The template therefore adds two pieces:

1. **Persistence** — feedback is written to the PostgreSQL `Feedback` table on click.
2. **CSV export** — an authenticated HTTP endpoint serves all users' feedback as a CSV download.

## Files involved

### `apps/chainlit/native_chat.py`

| Function | Description |
|---|---|
| `upsert_feedback()` | Stores feedback in the PostgreSQL `Feedback` table. Uses `ON CONFLICT ("stepId")` instead of `ON CONFLICT (id)`, so repeated clicks on the same step update the existing feedback rather than creating duplicates. Creates the unique index `Feedback_stepId_unique` idempotently. |
| `export_feedback_csv()` | Exports all feedback as CSV. Resolves the correct assistant answer via `LEFT JOIN LATERAL` (a child step of type `assistant_message`, because feedback hangs on `run` steps, which have no output of their own). The preceding user question is resolved with a LATERAL join as well. |

### `apps/chainlit/app.py`

| Piece | Description |
|---|---|
| `@cl.on_feedback` handler | Persists feedback events (Helpful/Not helpful + comment) to PostgreSQL via `upsert_feedback()`. Without this handler Chainlit shows the buttons but stores nothing. |
| `GET /export/feedback` | Authenticated endpoint serving a CSV download of all users' feedback. |
| Imports | `export_feedback_csv` and `upsert_feedback` from `native_chat`. |

## DB schema change

- **Unique index** `Feedback_stepId_unique` on `"Feedback"."stepId"` — prevents duplicate feedback rows per step.
- The index is created idempotently via `CREATE UNIQUE INDEX IF NOT EXISTS` on the first call to `upsert_feedback()`.

## Data-model background

Chainlit attaches feedback to **`run` steps** (the `on_message` wrapper), not to
`assistant_message` steps. But `run` steps carry no output of their own — the
actual answer sits in a **child step** of type `assistant_message` with
`parentId = run.id`.

The export query resolves this via:

```sql
LEFT JOIN LATERAL (
    SELECT cs.output FROM "Step" cs
    WHERE cs."parentId" = s.id AND cs.type = 'assistant_message'
    ORDER BY cs."startTime" DESC LIMIT 1
) child ON true
```

and uses `COALESCE(child.output, s.output)` as a fallback.

## CSV columns

| Column | Description |
|---|---|
| `username` | User name (from `User.identifier` via the thread) |
| `user_question` | The user's question (preceding `user_message` step) |
| `assistant_answer` | The assistant's answer (child `assistant_message` step) |
| `feedback_value` | `1.0` = Helpful, `0.0` = Not helpful |
| `feedback_comment` | Optional free-text comment |
| `answer_time` | Timestamp of the answer (ISO 8601) |
| `thread_id` | UUID of the chat thread |
| `feedback_id` | UUID of the feedback row |
| `step_id` | UUID of the step the feedback was attached to |

## Usage

### CSV export via the browser

```
http://localhost:8000/export/feedback
```

Requires authentication (login cookie or OAuth token) and returns the feedback of
all users.

### Ad-hoc query via PostgreSQL

```bash
docker exec rag-postgres psql -U chainlit -d chainlit -c '
SELECT u.identifier, f.value, f.comment, s."createdAt"
FROM "Feedback" f
JOIN "Step" s ON s.id = f."stepId"
JOIN "Thread" t ON t.id = s."threadId"
LEFT JOIN "User" u ON u.id = t."userId"
ORDER BY s."createdAt" DESC;
'
```

!!! note "Requires PostgreSQL"
    Persistence and export need the PostgreSQL data layer from the Compose stack
    (`make up`, see [Getting Started](getting-started.md)). Without a
    `DATABASE_URL` Chainlit still shows the feedback buttons, but there is nowhere
    to store them and therefore nothing to export.
