<p align="center">
  <img src="00_aisc/img/logo_aisc_bmftr.jpg" alt="AISC / BMFTR">
  <br>
  <img src="00_aisc/img/logo_fghgsd_60.png" alt="FGHGsD">
</p>

# Modular RAG Template

**🇩🇪 [Deutsche Version](README.de.md)** · 📖 **[Documentation](https://aihpi.github.io/pilotprojekt-rag-template/)**

A config-driven RAG application you point at your own documents. Everything that
usually gets hardcoded — models, chunking, retrieval, citations, prompt, tools —
lives in one YAML file, so a new knowledge assistant is a config change, not a
fork.

Built with [Chainlit](https://chainlit.io) (chat UI), [LiteLLM](https://litellm.ai)
(any OpenAI-compatible model gateway), [Qdrant](https://qdrant.tech) (vector
store) and [Docling](https://github.com/DS4SD/docling) (PDF parsing).

> **It runs out of the box.** Three open-access papers ship with the repo
> ([sources & licence](apps/chainlit/data/documents/SOURCES.md)), so you can chat
> with a working instance before configuring anything.

---

## Quickstart

```bash
git clone https://github.com/aihpi/pilotprojekt-rag-template.git
cd pilotprojekt-rag-template/apps/chainlit

cp .env.example .env      # put your gateway URL + API key in .env
docker compose up -d      # Qdrant + Postgres + ingest + app
```

Open <http://localhost:8000> (default login `admin` / `admin` — change it). The
default instance
[`examples/papers/rag.config.yaml`](apps/chainlit/examples/papers/rag.config.yaml)
ingests the three shipped papers with every feature enabled.

<details>
<summary><b>Without Docker</b></summary>

```bash
cd apps/chainlit
uv sync                                   # or: pip install -e .
docker run -p 6333:6333 qdrant/qdrant     # vector store

export RAG_CONFIG=examples/papers/rag.config.yaml
uv run python -m kb.ingest --dry-run      # inspect chunks, embeds nothing
uv run python -m kb.ingest                # embed + upsert
uv run chainlit run app.py                # http://localhost:8000
```
</details>

> **Model names are gateway-specific.** The example uses `gpt-4o-mini` and
> `text-embedding-3-large`. If your gateway rejects them, use its own names — the
> config has a commented block showing where.

## Use your own documents

```bash
cp apps/chainlit/examples/papers/rag.config.yaml apps/chainlit/my-rag.yaml
```

1. Drop your PDFs into `apps/chainlit/data/documents/`. Your files stay local —
   only the three examples are versioned, and you may delete them.
2. In `my-rag.yaml`, set a fresh `vector_store.collection`.
3. Re-ingest: `RAG_CONFIG=my-rag.yaml uv run python -m kb.ingest --recreate`

Other formats (Markdown, JSON, CSV, custom parsers) and chunking options:
[Adding your data](docs/adding-data.md).

## What you configure

| Block | What it controls |
|---|---|
| `models` | chat + embedding model, gateway, models offered in the UI picker |
| `vector_store` | Qdrant URL and collection |
| `data_sources[]` | where documents live, their format, per-source chunking |
| `chunking` | `fixed_size` · `heading` · `passthrough` · `semantic` · `docling_hybrid` |
| `retrieval` | `top_k`, score threshold, indexed and filterable metadata fields |
| `tools` | which [agentic tools](docs/tools.md) the model may call |
| `images` | [figure handling](docs/images.md): descriptions, inline placement, vision |
| `citation` | how a source reference is rendered and which fields it shows |
| `prompt` | system prompt (or [auto-generate](docs/prompts.md) one), starter questions |
| `app` | streaming, personalization, settings panel |

Full reference: [Configuration](docs/configuration.md) — generated from the schema,
so it cannot drift from the code.

## Features

- **Agentic retrieval** — the model chooses among five tools: semantic `search`,
  `list_documents`, `fetch_document` (a whole document, which is what summaries
  need), `expand_context`, `verify_claim`. Enable them per instance; `search`
  alone is classic RAG. → [docs](docs/tools.md)
- **Figures, not just text** — figures are extracted, described by a vision model
  and made searchable; a figure the answer discusses appears inline **above that
  paragraph**. → [docs](docs/images.md)
- **Tables survive ingestion** — Docling tables are serialized into their section
  instead of being dropped.
- **Clickable citations** — each claim carries a source that opens the original PDF
  at the right page; the format comes from config.
- **Self-writing system prompt** — with none configured, the app generates one from
  your indexed documents at startup and caches it. → [docs](docs/prompts.md)
- **Model picker and prompt editor** in the settings panel, persisted per user.
- **Chat history, feedback and CSV/ZIP export**, GitHub OAuth or local login.

## How it fits together

```
                        rag.config.yaml
                               │
  documents ──► kb/parsers ──► kb/chunkers ──► embeddings ──► Qdrant
  (pdf/md/json/csv/custom)                                      │
                                                                ▼
  Chainlit UI ◄── citations ◄── answer ◄── LLM + tools ◄── retrieval
```

A format lives in `kb/parsers/`, a chunking strategy in `kb/chunkers/`, a tool in
`tools/` — each is a small registry you extend by adding one file.
→ [Extending](docs/extending.md)

## Documentation

| Page | |
|---|---|
| [Getting started](docs/getting-started.md) | install, ingest, run |
| [Example corpus](docs/example-corpus.md) | what ships and how to swap it |
| [Adding your data](docs/adding-data.md) | formats, chunking, citations |
| [Agentic tools](docs/tools.md) | the five tools, writing your own |
| [Figures & images](docs/images.md) | `images.mode`, inline placement |
| [System prompts](docs/prompts.md) | generation, editing, model picker |
| [Configuration](docs/configuration.md) | full schema reference |
| [Field-mapping DSL](docs/field-mapping.md) | JSON/CSV → chunks |
| [Extending](docs/extending.md) | custom parsers, chunkers, tools |

Published in English and German at
**<https://aihpi.github.io/pilotprojekt-rag-template/>**, or locally via
`uv run --only-group docs mkdocs serve`.

## Limitations

- **Prototype.** Not security-audited — review it before production use.
- **Citations and follow-up questions are parsed from German markers**
  (`Quelle N: … (S.x)`, `Anschlussfragen:`), so set `language: de` for those to
  work. Your documents may be in any language.
- `images.mode: describe` costs one vision call per figure at ingest time.
- Changing the embedding model requires a re-ingest (`--recreate`).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Earlier project stages — the IT-Grundschutz
assistant, research notebooks and evaluation scripts — remain on the
`backup/pre-template-cleanup` branch.

## References

- [AI Service Centre Berlin Brandenburg (KI-Servicezentrum)](https://hpi.de/ki-servicezentrum/)
- [fghgsd.de](https://fghgsd.de)

## Licence

Code under the [MIT licence](LICENSE). The example papers are CC BY 4.0 — see
[SOURCES.md](apps/chainlit/data/documents/SOURCES.md).

---

## Acknowledgement
<img src="00_aisc/img/logo_bmftr_de.png" alt="BMFTR" style="width:170px;"/>

The [AI Service Centre Berlin Brandenburg](http://hpi.de/kisz) is funded by the
[German Federal Ministry of Research, Technology and Space](https://www.bmbf.de/)
under grant number 01IS22092.
