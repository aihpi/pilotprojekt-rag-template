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

### Added

- **`make check`, a setup test.** Answers the question that costs the most time when
  something does not work: is it my settings, my connection, or the AI service? It
  reads your settings, resolves and connects to the service host, then tries the chat,
  search and image models several times each. Every failure is reported as **numbered
  steps**, not a stack trace: which file to open, which line to look at, what to check,
  and to run it again afterwards.

  It also refuses to spend anything before checking the obvious. The placeholder values
  that `.env.example` ships with (`your-key`, `http://localhost:4000`) are recognised
  and named as the problem, because copying that file and never editing it is the
  commonest cause of "nothing works" and produces an error that says nothing about it.
  The key is shown masked, so a wrong or truncated key is visible without exposing it.

  Trying each model repeatedly is the point. The failure that misleads people is not
  "it broke" but "it broke sometimes": a connection losing one request in three lets a
  chat message through while making a document import fail dozens of times. A single
  attempt cannot tell those apart, so the report says `3 of 5 attempts worked`.

  The host is probed separately, with a plain name lookup and connection attempt,
  because litellm reports a misspelled address and a mid-request disconnect with the
  same "Connection error" message. Without that split, a typo in `LITELLM_BASE_URL`
  gets diagnosed as a VPN problem and sends people looking in the wrong place.

### Fixed

- **A stable internet connection is now stated as a requirement.** Reading documents
  makes hundreds of calls to the AI service, so a connection that drops occasionally
  fails often, while a single chat message works and hides the cause. Both READMEs and
  the getting-started pages say so, and `docs/troubleshooting.md` opens with how to
  tell an unstable connection apart from wrong settings.

- **The chat history could be corrupted, and was.** It is SQLite in WAL mode and lived
  under `.chainlit/`, which Docker bind-mounts from the host. WAL needs a shared-memory
  companion file and real POSIX locking, and Docker Desktop only emulates both across
  the macOS/Windows filesystem boundary, so a write interrupted at the wrong moment left
  `sqlite3.DatabaseError: database disk image is malformed` on every chat start. Hit
  during development after a series of container restarts; the file was recoverable with
  `sqlite3 old ".recover"`.

  Under Docker the database now lives on a named `chat_db` volume, which is a real Linux
  filesystem inside the VM where WAL behaves correctly. An existing history is carried
  over once on startup via SQLite's backup API, not a file copy: in WAL mode a database
  is several files and recently committed rows can still sit in the `-wal`, so copying
  only the main file loses them. A damaged legacy file is deliberately not carried over
  and is left in place, with the recovery command printed.

  Running without Docker is unaffected and keeps the old path: a native filesystem
  handles WAL fine. Linux hosts were never affected either, since a bind mount there is
  the same kernel filesystem.

- **Self-registration never worked.** `POST /auth/register` was broken three
  independent ways, each sufficient on its own.

  `app.py` uses `from __future__ import annotations`, so FastAPI receives the
  handler's annotation as the string `"RegisterRequest"` and resolves it against
  module globals. The model was declared inside the `on_app_startup` hook, making
  the name a local. FastAPI did not raise: it downgraded the parameter to a
  **query** parameter. Every JSON body was ignored (422 with
  `loc: ["query", "request"]`), a query parameter reached the handler as a `str`
  and raised (500), and `/openapi.json` returned 500 because the schema could not
  be built. The model is now defined at module level.

  `/openapi.json` still failed afterwards for an unrelated reason:
  `chainlit.auth.cookie.OAuth2PasswordBearerWithCookie` subclasses FastAPI's
  `SecurityBase` but never sets `self.model`, which the schema generator reads.
  The missing metadata is now supplied, guarded so it goes inert once Chainlit
  fixes it upstream.

  `create_user` carried two `ON CONFLICT` clauses, one per unique constraint.
  PostgreSQL allows a single clause per statement, so registration failed with a
  syntax error even once the body parsed. One untargeted `ON CONFLICT DO NOTHING`
  covers both constraints.

- **Documents added to the folder were never indexed, and nothing said so.** The
  `ingest` compose service exited successfully as soon as the target collection
  existed, so dropping a PDF into `data/documents/` and restarting did nothing at
  all. See *Changed* below for the new behaviour.

- **Docling's PDF models were re-downloaded on every ingest run.** Roughly 500 MB,
  each time. They cache under `/root/.cache`, and the ingest container mounted only
  the project directory and ran with `--rm`. A named `model_cache` volume now keeps
  them. The README claimed this happened "once per update", which was wrong twice
  over.

- **`pdf_options.ocr: true` failed late and unhelpfully.** The shipped image
  installs no apt packages by design, so it has no OCR engine. The run now stops in
  the first second, naming the package to install and pointing out that PDFs with a
  text layer need no OCR at all.

- **Oversized figures lost their description silently.** The ingest step sent
  full-resolution PNG to the vision model, so a large figure exceeded the gateway's
  body-size limit and came back HTTP 413. The exception was swallowed and the
  figure stored without a description. In the shipped `papers` example this hit
  1 of 79 figures (`Alam_2026` fig1, 1.11 MB encoded).

  The describe call now downscales and re-encodes as JPEG the way the answer-time
  path already did (that figure: 1.11 MB → 215 KB, new
  `images.describe_image_max_px`, default 1536).

- **A failed figure description is no longer retried zero times.** The vision call
  passes `num_retries`, so a passing rate limit no longer costs a figure its
  description permanently.

- **A figure with no description and no caption is no longer stored.** The guard
  meant to drop such chunks tested the assembled text, which always begins
  "Abbildung N (Seite X)" and is therefore never empty, so the guard could never
  fire. A figure now needs either a description or a real caption to be indexed,
  and each document reports how many descriptions failed.

  **Re-ingest only if you need it.** Existing collections are fine unless a figure
  actually failed. If your ingest log showed figure-description errors, or your
  documents contain unusually large figures, read them in once more with
  `--recreate` (see [Figures & images](docs/images.md)); this re-describes every
  figure and is charged accordingly.

### Changed

- **The document folders are watched, so changes need no command.** The app polls its
  source folders and indexes whatever was added, edited or deleted, within seconds
  and without a restart. Putting a file into the folder is the whole workflow.

  Two stages, so watching is genuinely free: a poll compares size and modification
  time and reads no file contents, and only a hint of change starts the real run that
  hashes and decides. Files modified in the last few seconds are left alone, so a
  large file still being copied is never read half-written, and one pass at a time is
  enforced. The work runs in a thread, so parsing a PDF cannot stall open chats.

  Polling rather than filesystem events: event delivery from a host bind mount into a
  container is unreliable, and a watcher that silently stops noticing is worse than
  one that looks every few seconds.

  On by default and **opt-out** via `DOCUMENT_WATCH=false`, so a `.env` copied before
  this existed gets the feature without being touched. `DOCUMENT_WATCH_INTERVAL` and
  `DOCUMENT_WATCH_SETTLE` tune it. This only became practical because of the change
  below; before it, any repeated run re-embedded everything.

- **Ingestion is incremental per file.** A plain `python -m kb.ingest` used to be
  all-or-nothing; it now reads only what changed. Every file is recorded with a
  sha256 of its contents, so a new file is ingested, an edited file is ingested
  again, and an unchanged file is skipped for free. Adding a document is therefore
  just putting it in the folder and starting the app.

  The record lives in a metadata point in the collection, keyed by file path rather
  than by the payload's `source_file`, because that key is parser-defined and
  inconsistent. Filtering happens in `kb/parsers/base.py:iter_source_files`, which
  every built-in parser already used, so the `ParserFn` extension point keeps its
  signature and custom parsers keep working.

  **Existing collections are adopted, not rebuilt.** A collection created before
  this has no record of its files. Treating it as empty would re-embed everything
  and, with `images.mode: describe`, re-describe every figure, so the first run
  instead records the current files without ingesting anything and cross-checks them
  against the collection, warning about any file that has no entries. Nothing to do
  and nothing charged.

  **A document deleted from the folder loses its entries.** Otherwise the assistant
  kept answering from, and citing, a file that no longer existed, with a source link
  that could only 404. Deletions are handled in the same run as additions, so
  replacing an entire corpus at once both removes the old entries and indexes the
  new documents.

  Two safeguards, because this makes ingestion destructive. A run limited by
  `--only` never prunes, since the sources it did not visit are not deletions. And
  if *no* file at all is found while the collection knows about some, nothing is
  removed and a warning explains why: an empty documents folder is almost always a
  mount that did not come up or a wrong `path`, not an intentional wipe. Use
  `--recreate` to empty a collection on purpose.

  Matching entries to a file relies on the `source_file` payload, so it works for
  PDF, Markdown and text sources. Entries are kept and reported, rather than
  silently left behind, in the two cases where they cannot be matched safely: a
  `json`/`csv` source whose `field_mapping` writes no `source_file`, and a name that
  another file on disk still uses.

- **Duplicate file names are now reported.** `doc_id`, and therefore each Qdrant
  point id, is derived from the file name alone, so two documents called `intro.pdf`
  in different folders of one collection collide and the second silently overwrites
  the first. That is not new and is not fixed here, because changing the derivation
  would invalidate every existing point id and force a full re-ingest of every
  instance. Each run now names the clashing files and says one of them is being lost.

  `--recreate` is unchanged and still the right tool after altering `chunking`,
  chunk sizes or `images.mode`. `--skip-if-exists` still works but is no longer
  useful, since it is what suppressed added, edited and deleted files alike.

- **Open-weight models everywhere by default.** All shipped configs, the schema
  defaults, `docker-compose.yml` and the docs now use `gpt-oss-120b` (Apache-2.0),
  `octen-embedding-8b` and `gemma-4-31b` instead of proprietary hosted models, so
  no instance depends on a single vendor. Model names remain gateway-specific;
  the configs point at `GET /v1/models` and list other open alternatives.

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
