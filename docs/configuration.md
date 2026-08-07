# Configuration Reference

Every field below is generated directly from the pydantic models in
`apps/chainlit/config/schema.py`, so this page never drifts from the code.

!!! note "Precedence"
    For each value: **explicit environment variable → YAML value → default**.
    Secrets and infrastructure (`LITELLM_API_KEY`, `QDRANT_URL`, …) are left
    `null` in YAML and supplied via environment variables. See `.env.example`.
    Relative paths resolve against the **config file's own directory**.

## Interface language

The interface is German or English, and **the browser decides**. Chainlit resolves
its own labels from `navigator.language` and ships no language picker, so the app's
own surfaces — welcome screen, starter questions, document-watcher badge, evaluation
badge and panel — follow that same signal instead of adding a second one that could
disagree with it. Anything that is not German gets English.

To pin one language for everyone, set it in `.chainlit/config.toml`:

```toml
[UI]
language = "de-DE"
```

Both badges honour that too. They read it from their status endpoints, because a
static file under `public/` cannot read the config itself.

Two things this deliberately does **not** cover:

- **The answer language is separate.** `language:` in your `rag.config.yaml` decides
  what the assistant writes. The `papers` example pins German because citations and
  follow-up questions are parsed with German markers, so an English interface can
  still return a German answer.
- **Judge reasons stay English.** The per-claim explanations in the evaluation panel
  come from RAGAS's own English prompts.

!!! note "`de-DE`, but not `de-AT`"
    Chainlit ships a `de-DE` catalogue and no plain `de`, so a browser reporting
    `de-AT` or `de-CH` gets English Chainlit labels while the app's own strings go
    German. Setting `[UI] language` removes the split.

Adding a third language means translating the string tables in
`public/eval-badge.js`, `public/ingest-status.js` and `eval_app/static/index.html`,
and adding a `chainlit_<locale>.md` welcome screen. Chat messages, the settings panel
and the citation panels are German-only for now; see `TODO.md`.

::: config.schema.RagConfig

::: config.schema.ModelsConfig

::: config.schema.VectorStoreConfig

::: config.schema.ChunkingConfig

::: config.schema.DataSourceConfig

::: config.schema.FieldMapping

::: config.schema.RecordSpec

::: config.schema.IterStep

::: config.schema.PdfOptions

::: config.schema.RetrievalConfig

::: config.schema.CitationConfig

::: config.schema.FilenameRule

::: config.schema.SourcesConfig

::: config.schema.PromptConfig

::: config.schema.ToolConfig

::: config.schema.ToolsConfig

::: config.schema.ImagesConfig

::: config.schema.EvaluationConfig

::: config.schema.ProfileConfig

::: config.schema.AppConfig

::: config.schema.UiTextConfig
