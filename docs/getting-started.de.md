# Erste Schritte

## 1. Klonen und installieren

```bash
git clone https://github.com/aihpi/pilotprojekt-rag-template.git
cd pilotprojekt-rag-template/apps/chainlit
uv sync            # uv nutzen, nicht pip: pip installiert falsche Paketversionen
cp .env.example .env   # LITELLM_*- und QDRANT_*-Secrets eintragen
```

## 2. Minimal-Konfiguration kopieren

Beginne mit der kleinsten funktionierenden Konfiguration und passe sie an:

```bash
cp examples/minimal/rag.config.yaml my-rag.yaml
```

```yaml
name: minimal-rag

models:
  chat_model: gpt-oss-120b
  embed_model: octen-embedding-8b

vector_store:
  collection: my_docs

data_sources:
  - name: docs
    path: ./data
    format: pdf
    glob: "*.pdf"
```

Richte `data_sources[].path` auf deine Dokumente, wähle eine `collection` und
setze die Modelle. Alle Felder findest du in der
[Konfigurationsreferenz](configuration.md).

Welche Konfiguration geladen wird, steuert die Umgebungsvariable `RAG_CONFIG`
(relativ zu `apps/chainlit/`):

```bash
export RAG_CONFIG=my-rag.yaml
```

## 3. Parsing mit `--dry-run` prüfen

`--dry-run` parst und chunkt **ohne zu embedden oder in Qdrant zu schreiben** und
gibt die ersten Chunks mit ihren Metadaten aus. Das ist der schnellste Weg, um
eine Konfiguration zu iterieren (besonders ein JSON/CSV-[Field-Mapping](field-mapping.md)):

```bash
python -m kb.ingest --dry-run --limit 5
```

```text
DRY RUN — parsed and chunked, nothing embedded or written.

  source 'docs' [pdf / fixed_size]: 12 sections -> 40 chunks

  TOTAL: 40 chunks across 1 source(s)
  ...
```

## 4. Ingestion

```bash
python -m kb.ingest              # embedded + upsertet in die konfigurierte Collection
python -m kb.ingest --recreate   # Collection komplett neu aufbauen
python -m kb.ingest --only docs  # nur bestimmte Quellen ingesten
```

!!! warning "`--skip-if-exists` vs. der Embedding-Modell-Wächter"
    `--skip-if-exists` **prüft nur, ob die Collection existiert** — es erkennt
    keine Konfigurationsänderungen. Beim ersten Ingest schreibt die Pipeline
    einen Sentinel mit dem verwendeten Embedding-Modell. Wenn du später den
    Inhalt oder das `embed_model` änderst, führe erneut mit **`--recreate`** aus
    (oder richte `vector_store.collection` auf einen neuen Namen). Ein
    abweichendes Embedding-Modell in eine bestehende Collection zu ingesten wird
    abgelehnt, da die Vektoren inkompatibel wären.

## 5. App starten

```bash
chainlit run app.py -w
# oder der gesamte Stack (Qdrant + Postgres + Auto-Ingest + App):
make up
```

## Doku lokal

```bash
uv run --only-group docs mkdocs serve   # http://127.0.0.1:8000
```
