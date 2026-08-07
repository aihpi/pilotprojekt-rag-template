# Konfigurationsreferenz

Jedes Feld unten wird direkt aus den pydantic-Modellen in
`apps/chainlit/config/schema.py` generiert, sodass diese Seite nie vom Code
abweicht. (Die Feldbeschreibungen stammen aus den Schema-Docstrings und sind auf
Englisch.)

!!! note "Vorrang"
    Für jeden Wert gilt: **explizite Umgebungsvariable → YAML-Wert → Default**.
    Secrets und Infrastruktur (`LITELLM_API_KEY`, `QDRANT_URL`, …) bleiben in der
    YAML `null` und werden über Umgebungsvariablen gesetzt, siehe `.env.example`.
    Relative Pfade werden relativ zum **Verzeichnis der Konfigurationsdatei** aufgelöst.

## Sprache der Oberfläche

Die Oberfläche ist deutsch oder englisch, und **der Browser entscheidet**. Chainlit
löst seine eigenen Beschriftungen über `navigator.language` auf und bringt keine
Sprachauswahl mit. Deshalb folgen die eigenen Flächen der App — Begrüßungsbildschirm,
Startfragen, Watcher-Badge, Bewertungs-Badge samt Panel — demselben Signal, statt ein
zweites einzuführen, das ihm widersprechen könnte. Alles, was nicht Deutsch ist,
bekommt Englisch.

Um eine Sprache für alle festzulegen, in `.chainlit/config.toml`:

```toml
[UI]
language = "de-DE"
```

Beide Badges beachten das ebenfalls. Sie erfahren es über ihre Status-Endpunkte, denn
eine statische Datei unter `public/` kann die Konfiguration nicht selbst lesen.

Zwei Dinge deckt das bewusst **nicht** ab:

- **Die Antwortsprache ist davon getrennt.** `language:` in Ihrer `rag.config.yaml`
  bestimmt, worin der Assistent schreibt. Das `papers`-Beispiel legt Deutsch fest,
  weil Zitate und Anschlussfragen über deutsche Marker geparst werden — eine
  englische Oberfläche kann also weiterhin eine deutsche Antwort liefern.
- **Judge-Begründungen bleiben englisch.** Die Erklärungen je Aussage im
  Bewertungs-Panel stammen aus den englischen Prompts von RAGAS.

!!! note "`de-DE`, aber nicht `de-AT`"
    Chainlit liefert einen `de-DE`-Katalog, aber kein einfaches `de`. Ein Browser, der
    `de-AT` oder `de-CH` meldet, bekommt daher englische Chainlit-Beschriftungen,
    während die eigenen Strings der App deutsch werden. `[UI] language` hebt die
    Trennung auf.

Eine dritte Sprache bedeutet: die String-Tabellen in `public/eval-badge.js`,
`public/ingest-status.js` und `eval_app/static/index.html` übersetzen und einen
Begrüßungsbildschirm `chainlit_<locale>.md` anlegen. Chat-Nachrichten, das
Einstellungs-Panel und die Quellen-Panels sind vorerst nur deutsch, siehe `TODO.md`.

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
