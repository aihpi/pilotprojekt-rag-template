# Getting Started

This page walks you from an empty folder to a running assistant that answers
questions about your own documents. Copy each command into a terminal in the
order shown.

## 1. Clone and install

```bash
git clone https://github.com/aihpi/pilotprojekt-rag-template.git
cd pilotprojekt-rag-template/apps/chainlit
uv sync            # use uv, not pip: pip installs the wrong package versions
cp .env.example .env   # fill in LITELLM_* and QDRANT_* secrets
```

The last line creates a file called `.env`. Open it and enter the address and
access key of your AI service. Nothing works until those are filled in.

## 2. Copy the minimal config

The settings file decides everything: which documents to read, which AI models to
use, how answers are put together. Start from the smallest working example and
change it:

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

Three things to adjust:

- `data_sources[].path` is the folder your documents are in.
- `collection` is a name you invent. It keeps this set of documents separate
  from any other.
- The two models must be names your AI service actually offers. If unsure, ask
  it for its list.

Every available setting is listed in the
[Configuration Reference](configuration.md).

Now tell the app to use your file. This has to be repeated in every new terminal
window:

```bash
export RAG_CONFIG=my-rag.yaml
```

## 3. Validate parsing with `--dry-run`

Before spending time and money on the real run, do a practice run. It reads your
documents and shows how they will be cut up, but **saves nothing and costs
nothing**:

```bash
python -m kb.ingest --dry-run --limit 5
```

```text
DRY RUN: parsed and chunked, nothing embedded or written.

  source 'docs' [pdf / fixed_size]: 12 sections -> 40 chunks

  TOTAL: 40 chunks across 1 source(s)
  ...
```

If the number of pieces is 0, the app found no documents. Check the `path` and
`glob` lines in your settings file. This practice run is the fastest way to get a
config right, especially for JSON/CSV files
([field-mapping](field-mapping.md)).

## 4. Ingest

Now do it for real. The app reads every document and stores it so it can be
searched. Depending on how many documents you have, this can take a while:

```bash
python -m kb.ingest              # embeds + upserts into the configured collection
python -m kb.ingest --recreate   # rebuild the collection from scratch
python -m kb.ingest --only docs  # ingest specific sources
```

Use the first line the first time. Use `--recreate` whenever you have changed
your documents or your settings and want to start clean.

!!! warning "`--skip-if-exists` vs. the embed-model sentinel"
    `--skip-if-exists` **only checks whether the collection exists**. It does
    not notice that you changed anything. So if you edit your documents or
    settings, that option will skip the work and you will keep getting old
    answers.

    There is one thing the app does catch: on the first run it notes down which
    model made your text searchable. If you later switch to a different one, it
    refuses rather than mixing incompatible data. Use **`--recreate`**, or give
    `vector_store.collection` a new name.

## 5. Run the app

```bash
chainlit run app.py -w
# or the whole stack (Qdrant + Postgres + auto-ingest + app):
make up
```

Then open <http://localhost:8000> and ask a question. Under each answer you will
see its sources. Click one and the original document opens at the right page.

## Docs locally

```bash
uv run --only-group docs mkdocs serve   # http://127.0.0.1:8000
```
