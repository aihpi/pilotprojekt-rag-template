# Konfigurationsreferenz

Jedes Feld unten wird direkt aus den pydantic-Modellen in
`apps/chainlit/config/schema.py` generiert, sodass diese Seite nie vom Code
abweicht. (Die Feldbeschreibungen stammen aus den Schema-Docstrings und sind auf
Englisch.)

!!! note "Vorrang"
    Für jeden Wert gilt: **explizite Umgebungsvariable → YAML-Wert → Default**.
    Secrets und Infrastruktur (`LITELLM_API_KEY`, `QDRANT_URL`, …) bleiben in der
    YAML `null` und werden über Umgebungsvariablen gesetzt — siehe `.env.example`.
    Relative Pfade werden relativ zum **Verzeichnis der Konfigurationsdatei** aufgelöst.

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

::: config.schema.ProfileConfig

::: config.schema.AppConfig

::: config.schema.UiTextConfig
