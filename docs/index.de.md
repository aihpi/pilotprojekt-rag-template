# RAG-Template

Ein **konfigurationsgesteuertes, modulares RAG-Template**. Eine einzige
deklarative YAML-Datei beschreibt eine komplette Instanz — Datenquellen,
Chunking, Chat- und Embedding-Modelle, Vektordatenbank, Retrieval, Zitate,
Prompt und Rollen — sodass dieselbe Codebasis ohne Codeänderung auf einen neuen
Datenbestand gerichtet werden kann.

Stack: **Chainlit** (Chat-UI) · **LiteLLM** (beliebiger Chat-/Embedding-Anbieter) ·
**Qdrant** (Vektordatenbank).

## Was per Konfiguration änderbar ist

| Bereich | Ort | Wie |
|---|---|---|
| **Daten** | `data_sources[]` | PDF (Docling), `txt`/`md` oder strukturiertes `json`/`csv` über ein Field-Mapping |
| **Chunking** | `chunking.strategy` | `fixed_size`, `heading`, `passthrough`, `semantic` oder `docling_hybrid` (pro Quelle überschreibbar) |
| **Chat-Modell** | `models.chat_model` | beliebiger LiteLLM-`provider/model`-String |
| **Embedding-Modell** | `models.embed_model` | beliebiges LiteLLM-Embedding-Modell |
| **Zitate** | `citation.*` | Segment-Vorlagen, Zitat-Schlüsselwort, Seitenabkürzung |
| **Prompt / Rollen** | `prompt`, `profiles` | System-Prompt, Startfragen, optionale Rollen zur Retrieval-Einschränkung |
| **Agentische Tools** | `tools.enabled` | welche der zuschaltbaren Tools (`search`, `list_documents`, `fetch_document`, `expand_context`, `verify_claim`) das Modell aufrufen darf |
| **Abbildungen** | `images.mode` | `none`, `describe` (durchsuchbare Abbildungsbeschreibungen) oder `attach` (Bildpixel an ein Vision-Modell) |

## Wie alles zusammenspielt

```
data_sources ─► Parser (nach Format) ─► Chunker (nach Strategie) ─► Embedding ─► Qdrant
                                                                                 │
Nutzerfrage ─► Retrieval (top_k, optionale Filter) ◄─────────────────────────────┘
           └─► LLM-Tool-Schleife ─► Antwort + konfigurationsgesteuerte Zitate
```

- **[Erste Schritte](getting-started.md)** — klonen, konfigurieren, ingesten, starten.
- **[Beispielkorpus](example-corpus.md)** — die mitgelieferte Instanz, auf der ein frischer Clone läuft.
- **[Daten hinzufügen](adding-data.md)** — eigene Datenquellen deklarieren.
- **[Agentische Tools](tools.md)** — die zuschaltbaren Tools, die das Modell aufrufen kann.
- **[Abbildungen](images.md)** — PDF-Abbildungen beschreiben oder anhängen.
- **[System-Prompts](prompts.md)** — handgeschrieben oder automatisch generiert.
- **[Konfigurationsreferenz](configuration.md)** — jedes Feld, aus dem Schema generiert.
- **[Field-Mapping-DSL](field-mapping.md)** — JSON/CSV in Chunks umwandeln.
- **[Erweitern](extending.md)** — eigenen Parser oder Chunker hinzufügen.
- **[Feedback-Export](feedback-export.md)** — Nutzerbewertungen sammeln und exportieren.
