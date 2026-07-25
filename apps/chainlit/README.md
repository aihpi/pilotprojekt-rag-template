# Chainlit RAG App

This folder contains the config-driven Chainlit RAG app:

- a typed config layer (`config/`) — one YAML per instance, selected by `RAG_CONFIG`,
- a pluggable ingestion pipeline (`kb/`) — parsers × chunkers → embed → Qdrant,
- the agentic tools the model may call (`tools/`),
- the chat UI (`app.py`), and runnable instances under `examples/`.

The active instance is chosen by the `RAG_CONFIG` env var. A fresh clone defaults
to `examples/papers/rag.config.yaml` (a small open-access paper corpus with all
features enabled); `config/default.yaml` is the neutral baseline, and
`my-rag.yaml` is the gitignored slot for your own instance.

**This file is the short operations guide.** For everything else — configuring an
instance, adding data, tools, figures, prompts, the full config reference — see
the [project docs](../../docs/index.md).

## Quickstart (Docker Compose)

From `apps/chainlit`:

```bash
cp .env.example .env      # set LITELLM_BASE_URL + LITELLM_API_KEY, pick RAG_CONFIG
make up                   # docker compose up -d --build
make logs                 # follow chainlit logs
```

Then open <http://localhost:8000>.

The stack starts `chainlit` (:8000), `qdrant` (:6333), `postgres` (:5432, native
Chainlit thread history), `langflow` (:7860, optional) and a one-shot `ingest`
container that runs `python -m kb.ingest --config "$RAG_CONFIG"` before Chainlit
comes up. Inside Docker, services talk over service DNS names (`qdrant`,
`postgres`), not `localhost`; Compose forces `QDRANT_URL=http://qdrant:6333`.

Day-to-day:

```bash
make down         # stop stack
make reingest     # force recreate + reingest the collection
make ps           # service status
```

## Local (no Docker)

Needs Python 3.12+, a reachable Qdrant, and an OpenAI-compatible LiteLLM endpoint.

```bash
uv sync                                  # or: make local-install
docker run -d --name qdrant -p 6333:6333 -v qdrant_data:/qdrant/storage qdrant/qdrant
uv run python -m kb.ingest --dry-run     # inspect chunks, no embedding
uv run python -m kb.ingest               # embed + upsert
uv run chainlit run app.py -w            # add --port 8001 if 8000 is taken
```

## Environment

`.env` holds **secrets, infrastructure and the config selector** only —
per-instance settings (models, collection, chunking, prompt, citations, tools,
images) live in the YAML. See `.env.example` for the annotated list; the
essentials are:

- `RAG_CONFIG` — which YAML to load (relative to `apps/chainlit/`)
- `LITELLM_BASE_URL`, `LITELLM_API_KEY`
- `QDRANT_URL`, `QDRANT_API_KEY`
- `DATABASE_URL`, `CHAINLIT_AUTH_SECRET`, `CHAINLIT_AUTH_USERNAME`/`_PASSWORD`
  (native sidebar history + login — change the `admin/admin` default)
- `INGEST_RECREATE`, `INGEST_BATCH_SIZE`, `INGEST_MAX_BATCH_CHARS`,
  `INGEST_DOCLING_JSON_DIR` — auto-ingestion controls
- `LANGFLOW_ENABLED`, `LANGFLOW_BASE_URL`, `LANGFLOW_FLOW_ID`,
  `LANGFLOW_API_KEY` — exposes `langflow_agent` as an extra tool

Anything under "optional overrides" in `.env.example` (`CHAT_MODEL`, `TOP_K`, …)
takes precedence over the YAML, but you rarely need it.

## Ingestion

```bash
uv run python -m kb.ingest --dry-run          # parse + chunk only, prints samples
uv run python -m kb.ingest --recreate         # rebuild the collection from scratch
uv run python -m kb.ingest --only docs        # a single data source
uv run python -m kb.ingest --skip-if-exists   # no-op if the collection exists
uv run python -m kb.ingest --config examples/minimal/rag.config.yaml
```

> `--skip-if-exists` only checks that the collection exists. After changing the
> config's content or its `embed_model`, use `--recreate` (or a new
> `vector_store.collection`) — a mismatched embed model is refused by the
> sentinel guard, because the vectors would be incompatible.

For slow PDF corpora you can pre-convert once with Docling's own CLI
(`docling --to json --output <dir> <pdf-dir>`) and point
`pdf_options.docling_json_dir` at the result — see
[Adding your data](../../docs/adding-data.md).

## Chat history & export

Two layers coexist:

- **Native Chainlit thread history** (left sidebar) — Postgres via `DATABASE_URL`
  plus login.
- **Local SQLite log** used by the slash commands — DB at
  `.chainlit/chat_history.sqlite3`, exports written to `.files/chat_exports`
  (both overridable via `CHAT_DB_PATH` / `CHAT_EXPORT_DIR`).

In the chat UI: `/history`, `/history <session_id>`, `/export`,
`/export <session_id>`, `/export all` (OpenAI-format JSON / JSONL).

For a bulk download there is an **Export all chats** button in the left sidebar
(injected by `public/custom.js`), which calls the `GET /export/all-chats` route
and returns an OpenAI-format JSONL file. Admins can also export collected
ratings via `GET /export/feedback` — see
[Feedback export](../../docs/feedback-export.md).

## Tests

```bash
RAG_CONFIG=config/default.yaml uv run pytest tests/ -q
```

## Troubleshooting

- **`Connection refused` to Qdrant** — not running, or wrong `QDRANT_URL`
  (`http://qdrant:6333` inside Docker).
- **Embedding 400 / context window exceeded** — lower `INGEST_MAX_BATCH_CHARS`
  and/or `INGEST_BATCH_SIZE`.
- **Large payload 400 from Qdrant** — lower the ingest batch size (256 → 128).
- **Port 8000 already in use** — `chainlit run app.py -w --port 8001`.
- **Citations point at the wrong text** — re-ingest with `--recreate`.
- **`401 Unauthorized` / `404 Source not found` on `/sources/...`** — log in
  again, and check the file lives under `sources.data_dir` with an extension
  listed in `sources.served_extensions`.
