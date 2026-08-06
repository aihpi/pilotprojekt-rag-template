# Configuration Reference

Every field below is generated directly from the pydantic models in
`apps/chainlit/config/schema.py`, so this page never drifts from the code.

!!! note "Precedence"
    For each value: **explicit environment variable → YAML value → default**.
    Secrets and infrastructure (`LITELLM_API_KEY`, `QDRANT_URL`, …) are left
    `null` in YAML and supplied via environment variables. See `.env.example`.
    Relative paths resolve against the **config file's own directory**.

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
