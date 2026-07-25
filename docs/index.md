# RAG Template

A **config-driven, modular RAG template**. One declarative YAML file describes an
entire instance — data sources, chunking, chat & embedding models, vector store,
retrieval, citations, prompt and profiles — so you can point the same codebase at
a new corpus without touching code.

Stack: **Chainlit** (chat UI) · **LiteLLM** (any chat/embedding provider) ·
**Qdrant** (vector store).

## What you can change from config

| Concern | Where | How |
|---|---|---|
| **Data** | `data_sources[]` | PDF (Docling), `txt`/`md`, or structured `json`/`csv` via a field-mapping |
| **Chunking** | `chunking.strategy` | `fixed_size`, `heading`, `passthrough`, `semantic`, or `docling_hybrid` (per-source overridable) |
| **Chat model** | `models.chat_model` | any LiteLLM `provider/model` string |
| **Embedding model** | `models.embed_model` | any LiteLLM embedding model |
| **Citations** | `citation.*` | segment templates, token word, page abbreviation |
| **Prompt / roles** | `prompt`, `profiles` | system prompt, starters, optional retrieval-scoping roles |
| **Agentic tools** | `tools.enabled` | which of the pluggable tools (`search`, `list_documents`, `fetch_document`, `expand_context`, `verify_claim`) the model may call |
| **Figures** | `images.mode` | `none`, `describe` (searchable figure descriptions) or `attach` (figure pixels to a vision model) |

## How it fits together

```
data_sources ─► parser (by format) ─► chunker (by strategy) ─► embed ─► Qdrant
                                                                         │
user question ─► retrieve (top_k, optional filters) ◄────────────────────┘
             └─► LLM tool loop ─► answer + config-driven citations
```

- **[Getting Started](getting-started.md)** — clone, configure, ingest, run.
- **[Example Corpus](example-corpus.md)** — the bundled instance a fresh clone runs on.
- **[Adding Your Data](adding-data.md)** — declare your own sources.
- **[Agentic Tools](tools.md)** — the pluggable tools the model can call.
- **[Figures & Images](images.md)** — describe or attach PDF figures.
- **[System Prompts](prompts.md)** — hand-written or auto-generated.
- **[Configuration Reference](configuration.md)** — every field, generated from the schema.
- **[Field-Mapping DSL](field-mapping.md)** — turn JSON/CSV into chunks.
- **[Extending](extending.md)** — add a custom parser or chunker.
- **[Feedback Export](feedback-export.md)** — collect and export user ratings.
