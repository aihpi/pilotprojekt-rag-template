<p align="center">
  <img src="00_aisc/img/logo_aisc_bmftr.jpg" alt="AISC / BMFTR">
</p>

# Modulares RAG-Template

**🇬🇧 [English version](README.md)** · 📖 **[Dokumentation](https://aihpi.github.io/pilotprojekt-rag-template/de/)**

Eine konfigurationsgesteuerte RAG-Anwendung, die du auf eigene Dokumente
richtest. Alles, was üblicherweise im Code festgeschrieben wird — Modelle,
Chunking, Retrieval, Zitate, Prompt, Tools — steht in einer YAML-Datei. Ein neuer
Wissens-Assistent ist damit eine Konfigurationsänderung, kein Fork.

Stack: [Chainlit](https://chainlit.io) (Chat-UI), [LiteLLM](https://litellm.ai)
(beliebiges OpenAI-kompatibles Modell-Gateway), [Qdrant](https://qdrant.tech)
(Vektordatenbank) und [Docling](https://github.com/DS4SD/docling) (PDF-Parsing).

> **Läuft direkt nach dem Clonen.** Drei Open-Access-Paper liegen im Repository
> ([Quellen & Lizenz](apps/chainlit/data/documents/SOURCES.md)) — du kannst also
> mit einer funktionierenden Instanz chatten, bevor du irgendetwas konfigurierst.

---

## Schnellstart

```bash
git clone https://github.com/aihpi/pilotprojekt-rag-template.git
cd pilotprojekt-rag-template/apps/chainlit

cp .env.example .env      # Gateway-URL + API-Key in .env eintragen
docker compose up -d      # Qdrant + Postgres + Ingest + App
```

<http://localhost:8000> öffnen (Standard-Login `admin` / `admin` — ändern!). Die
Default-Instanz
[`examples/papers/rag.config.yaml`](apps/chainlit/examples/papers/rag.config.yaml)
ingestiert die drei mitgelieferten Paper mit allen Features.

<details>
<summary><b>Ohne Docker</b></summary>

```bash
cd apps/chainlit
uv sync                                   # oder: pip install -e .
docker run -p 6333:6333 qdrant/qdrant     # Vektordatenbank

export RAG_CONFIG=examples/papers/rag.config.yaml
uv run python -m kb.ingest --dry-run      # Chunks prüfen, nichts embedden
uv run python -m kb.ingest                # embedden + hochladen
uv run chainlit run app.py                # http://localhost:8000
```
</details>

> **Modellnamen sind gateway-abhängig.** Das Beispiel nutzt `gpt-4o-mini` und
> `text-embedding-3-large`. Lehnt dein Gateway diese ab, trage dessen eigene
> Namen ein — die Config enthält dafür einen kommentierten Block.

## Eigene Dokumente verwenden

```bash
cp apps/chainlit/examples/papers/rag.config.yaml apps/chainlit/my-rag.yaml
```

1. Eigene PDFs nach `apps/chainlit/data/documents/` legen. Eigene Dateien bleiben
   lokal — nur die drei Beispiele sind versioniert und dürfen gelöscht werden.
2. In `my-rag.yaml` eine neue `vector_store.collection` setzen.
3. Neu ingesten: `RAG_CONFIG=my-rag.yaml uv run python -m kb.ingest --recreate`

Weitere Formate (Markdown, JSON, CSV, eigene Parser) und Chunking-Optionen:
[Daten hinzufügen](docs/adding-data.de.md).

## Was du konfigurierst

| Bereich | Wofür |
|---|---|
| `models` | Chat- + Embedding-Modell, Gateway, Auswahl im UI-Selektor |
| `vector_store` | Qdrant-URL und Collection |
| `data_sources[]` | wo die Dokumente liegen, Format, Chunking pro Quelle |
| `chunking` | `fixed_size` · `heading` · `passthrough` · `semantic` · `docling_hybrid` |
| `retrieval` | `top_k`, Score-Schwelle, indexierte und filterbare Metadatenfelder |
| `tools` | welche [agentischen Tools](docs/tools.de.md) das Modell aufrufen darf |
| `images` | [Abbildungen](docs/images.de.md): Beschreibungen, Inline-Anzeige, Vision |
| `citation` | wie eine Quellenangabe gerendert wird und welche Felder erscheinen |
| `prompt` | System-Prompt (oder [automatisch erzeugen](docs/prompts.de.md)), Startfragen |
| `app` | Streaming, Personalisierung, Einstellungs-Panel |

Vollständige Referenz: [Konfiguration](docs/configuration.de.md) — aus dem Schema
generiert und damit immer aktuell.

## Funktionen

- **Agentisches Retrieval** — das Modell wählt aus fünf Tools: semantische
  `search`, `list_documents`, `fetch_document` (ganzes Dokument — genau das, was
  Zusammenfassungen brauchen), `expand_context`, `verify_claim`. Pro Instanz
  aktivierbar; nur `search` entspricht klassischem RAG. → [Doku](docs/tools.de.md)
- **Abbildungen, nicht nur Text** — Abbildungen werden extrahiert, von einem
  Vision-Modell beschrieben und durchsuchbar gemacht; eine Abbildung, die die
  Antwort behandelt, erscheint **direkt über dem zugehörigen Absatz**.
  → [Doku](docs/images.de.md)
- **Tabellen überleben den Ingest** — Docling-Tabellen werden in ihren Abschnitt
  serialisiert statt verworfen.
- **Klickbare Zitate** — jede Aussage trägt eine Quelle, die das Original-PDF auf
  der richtigen Seite öffnet; das Format kommt aus der Config.
- **Selbstschreibender System-Prompt** — ist keiner konfiguriert, erzeugt die App
  beim Start einen aus den indexierten Dokumenten. → [Doku](docs/prompts.de.md)
- **Modell-Selektor und Prompt-Editor** im Einstellungs-Panel, pro Nutzer gespeichert.
- **Chatverlauf, Feedback und CSV-/ZIP-Export**, GitHub-OAuth oder lokaler Login.

## Wie alles zusammenspielt

```
                        rag.config.yaml
                               │
  Dokumente ──► kb/parsers ──► kb/chunkers ──► Embeddings ──► Qdrant
  (pdf/md/json/csv/custom)                                      │
                                                                ▼
  Chainlit-UI ◄── Zitate ◄── Antwort ◄── LLM + Tools ◄── Retrieval
```

Ein Format lebt in `kb/parsers/`, eine Chunking-Strategie in `kb/chunkers/`, ein
Tool in `tools/` — jeweils eine kleine Registry, die man um eine Datei erweitert.
→ [Erweitern](docs/extending.de.md)

## Dokumentation

| Seite | |
|---|---|
| [Erste Schritte](docs/getting-started.de.md) | Installation, Ingest, Start |
| [Beispielkorpus](docs/example-corpus.de.md) | was mitgeliefert wird und wie man es tauscht |
| [Daten hinzufügen](docs/adding-data.de.md) | Formate, Chunking, Zitate |
| [Agentische Tools](docs/tools.de.md) | die fünf Tools, eigene schreiben |
| [Abbildungen](docs/images.de.md) | `images.mode`, Inline-Platzierung |
| [System-Prompts](docs/prompts.de.md) | Generierung, Bearbeitung, Modell-Selektor |
| [Konfiguration](docs/configuration.de.md) | vollständige Schema-Referenz |
| [Field-Mapping-DSL](docs/field-mapping.de.md) | JSON/CSV → Chunks |
| [Erweitern](docs/extending.de.md) | eigene Parser, Chunker, Tools |

Veröffentlicht auf Deutsch und Englisch unter
**<https://aihpi.github.io/pilotprojekt-rag-template/>**, lokal via
`uv run --only-group docs mkdocs serve`.

## Einschränkungen

- **Prototyp.** Nicht sicherheitsauditiert — vor Produktiveinsatz prüfen.
- **Zitate und Anschlussfragen werden über deutsche Marker geparst**
  (`Quelle N: … (S.x)`, `Anschlussfragen:`). Dafür `language: de` setzen; die
  Dokumente selbst dürfen in jeder Sprache sein.
- `images.mode: describe` kostet beim Ingest einen Vision-Aufruf pro Abbildung.
- Ein Wechsel des Embedding-Modells erfordert einen Re-Ingest (`--recreate`).

## Mitwirken

Siehe [CONTRIBUTING.md](CONTRIBUTING.md). Frühere Projektstände — der
IT-Grundschutz-Assistent, Forschungs-Notebooks und Evaluations-Skripte — liegen im
Branch `backup/pre-template-cleanup`.

## Referenzen

- [AI Service Centre Berlin Brandenburg (KI-Servicezentrum)](https://hpi.de/ki-servicezentrum/)
- [fghgsd.de](https://fghgsd.de)

## Lizenz

Der Code steht unter der [MIT-Lizenz](LICENSE). Die Beispiel-Paper sind CC BY 4.0 —
siehe [SOURCES.md](apps/chainlit/data/documents/SOURCES.md).

---

## Danksagung
<img src="00_aisc/img/logo_bmftr_de.png" alt="BMFTR" style="width:170px;"/>

Das [AI Service Centre Berlin Brandenburg](http://hpi.de/kisz) wird vom
[Bundesministerium für Forschung, Technologie und Raumfahrt](https://www.bmbf.de/)
unter dem Förderkennzeichen 01IS22092 gefördert.
