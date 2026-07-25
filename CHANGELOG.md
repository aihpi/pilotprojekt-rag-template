# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

Turned a single-purpose domain assistant into a reusable, config-driven RAG
template: one declarative YAML describes an entire instance, so the same codebase
can be pointed at a new corpus without touching Python.

### Added

- **YAML-driven configuration.** One file per instance (selected by `RAG_CONFIG`)
  describes data sources, chunking, models, vector store, retrieval, citations,
  prompt, tools, figures and profiles. Typed and validated by pydantic models in
  `apps/chainlit/config/schema.py`; environment variables still override single
  values, and relative paths resolve against the config file's own directory.
- **Parser and chunker registries.** Built-in `pdf`, `txt`/`md`, `json` and `csv`
  parsers, a declarative field-mapping DSL for structured records, plus five
  chunking strategies — `fixed_size`, `heading`, `passthrough`, `semantic` and
  `docling_hybrid` — selectable globally or per data source. Custom parsers and
  chunkers register by name.
- **Agentic tools.** Five pluggable tools the model may call, enabled per instance
  via `tools.enabled`: `search`, `list_documents`, `fetch_document`,
  `expand_context` and `verify_claim`.
- **Figure handling.** `images.mode` chooses between dropping PDF figures
  (`none`), indexing model-written figure descriptions as searchable and citable
  chunks (`describe`), or additionally feeding figure pixels to a vision-capable
  chat model (`attach`). Cited figures render inline in the answer or as
  thumbnails.
- **Automatic system-prompt generation.** When no prompt is configured, one is
  generated from a sample of the indexed chunks and cached next to the config.
- **Chat model selector.** Users can switch the chat model from the UI settings,
  within the models the instance allows.
- **Bundled example corpus.** `examples/papers/` (three open-access papers, all
  features enabled) is the default, so a fresh clone is runnable; also
  `examples/minimal/` as a starting point and `config/default.yaml` as the neutral
  baseline.
- **Documentation site.** MkDocs Material site (English and German) with a
  configuration reference generated from the schema.

### Removed

The following were dropped as domain- or workflow-specific. They remain available
in the `backup/pre-template-cleanup` branch.

- The IT-Grundschutz example instance, its custom parser and its documentation
  pages.
- The `data/` directory of source documents at the repo root.
- The `scripts/` and `notebooks/` directories.
- Orphaned helper scripts, including `export_docling_md.py` (use Docling's own
  CLI: `docling --to json <pdf>`) and `export_chats.py` (use the
  `/export/all-chats` route or the sidebar export button).
