# Getting Started

## 1. Clone and install

```bash
git clone https://github.com/aihpi/pilotprojekt-rag-template.git
cd pilotprojekt-rag-template/apps/chainlit
uv sync            # or: pip install -e .
cp .env.example .env   # fill in LITELLM_* and QDRANT_* secrets
```

## 2. Copy the minimal config

Start from the smallest working config and edit it:

```bash
cp examples/minimal/rag.config.yaml my-rag.yaml
```

```yaml
name: minimal-rag

models:
  chat_model: gpt-oss-120b
  embed_model: octen-embedding-8b

vector_store:
  collection: my_docs

data_sources:
  - name: docs
    path: ./data
    format: pdf
    glob: "*.pdf"
```

Point `data_sources[].path` at your documents, pick a `collection`, and set the
models. See the [Configuration Reference](configuration.md) for every field.

Tell the app which config to load via the `RAG_CONFIG` environment variable
(relative to `apps/chainlit/`):

```bash
export RAG_CONFIG=my-rag.yaml
```

## 3. Validate parsing with `--dry-run`

`--dry-run` parses and chunks **without embedding or writing to Qdrant**, and
prints the first chunks with their metadata. This is the fastest way to iterate
on a config (especially a JSON/CSV [field-mapping](field-mapping.md)):

```bash
python -m kb.ingest --dry-run --limit 5
```

```text
DRY RUN — parsed and chunked, nothing embedded or written.

  source 'docs' [pdf / fixed_size]: 12 sections -> 40 chunks

  TOTAL: 40 chunks across 1 source(s)
  ...
```

## 4. Ingest

```bash
python -m kb.ingest              # embeds + upserts into the configured collection
python -m kb.ingest --recreate   # rebuild the collection from scratch
python -m kb.ingest --only docs  # ingest specific sources
```

!!! warning "`--skip-if-exists` vs. the embed-model sentinel"
    `--skip-if-exists` **only checks whether the collection exists** — it does
    not detect config changes. On first ingest the pipeline writes a sentinel
    recording the embedding model. If you later change the content or the
    `embed_model`, re-run with **`--recreate`** (or point `vector_store.collection`
    at a new name). Ingesting a different embedding model into an existing
    collection is refused, because the vectors would be incompatible.

## 5. Run the app

```bash
chainlit run app.py -w
# or the whole stack (Qdrant + Postgres + auto-ingest + app):
make up
```

## Docs locally

```bash
uv run --only-group docs mkdocs serve   # http://127.0.0.1:8000
```
