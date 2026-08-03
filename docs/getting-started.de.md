# Erste Schritte

Diese Seite führt dich von einem leeren Ordner zu einem laufenden Assistenten,
der Fragen zu deinen eigenen Dokumenten beantwortet. Kopiere die Befehle der
Reihe nach in ein Terminal.

## 1. Klonen und installieren

```bash
git clone https://github.com/aihpi/pilotprojekt-rag-template.git
cd pilotprojekt-rag-template/apps/chainlit
uv sync            # uv nutzen, nicht pip: pip installiert falsche Paketversionen
cp .env.example .env   # LITELLM_*- und QDRANT_*-Secrets eintragen
```

Die letzte Zeile legt eine Datei namens `.env` an. Öffne sie und trage Adresse
und Zugangsschlüssel deines KI-Dienstes ein. Ohne diese Angaben funktioniert
nichts.

## 2. Minimal-Konfiguration kopieren

Die Einstellungsdatei entscheidet über alles: welche Dokumente gelesen werden,
welche KI-Modelle zum Einsatz kommen, wie Antworten aufgebaut sind. Beginne mit
dem kleinsten funktionierenden Beispiel und passe es an:

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

Drei Dinge musst du anpassen:

- `data_sources[].path` ist der Ordner, in dem deine Dokumente liegen.
- `collection` ist ein Name, den du dir ausdenkst. Er hält diesen Satz Dokumente
  von allen anderen getrennt.
- Die beiden Modelle müssen Namen sein, die dein KI-Dienst tatsächlich anbietet.
  Im Zweifel dort die Liste erfragen.

Alle verfügbaren Einstellungen stehen in der
[Konfigurationsreferenz](configuration.md).

Jetzt sagst du der App, welche Datei sie nutzen soll. Das musst du in jedem neuen
Terminal-Fenster wiederholen:

```bash
export RAG_CONFIG=my-rag.yaml
```

## 3. Parsing mit `--dry-run` prüfen

Bevor du Zeit und Geld in den echten Durchlauf steckst, mach einen Probelauf. Er
liest deine Dokumente und zeigt, wie sie zerteilt werden, **speichert aber nichts
und kostet nichts**:

```bash
python -m kb.ingest --dry-run --limit 5
```

```text
DRY RUN: parsed and chunked, nothing embedded or written.

  source 'docs' [pdf / fixed_size]: 12 sections -> 40 chunks

  TOTAL: 40 chunks across 1 source(s)
  ...
```

Steht dort 0 Stücke, hat die App keine Dokumente gefunden. Prüfe dann `path` und
`glob` in deiner Einstellungsdatei. Dieser Probelauf ist der schnellste Weg zu
einer richtigen Konfiguration, besonders bei JSON/CSV-Dateien
([Field-Mapping](field-mapping.md)).

## 4. Ingestion

Jetzt der echte Durchlauf. Die App liest jedes Dokument und speichert es
durchsuchbar ab. Je nach Menge dauert das eine Weile:

```bash
python -m kb.ingest              # embedded + upsertet in die konfigurierte Collection
python -m kb.ingest --recreate   # Collection komplett neu aufbauen
python -m kb.ingest --only docs  # nur bestimmte Quellen ingesten
```

Beim ersten Mal nimmst du die erste Zeile. `--recreate` nutzt du immer dann, wenn
du Dokumente oder Einstellungen geändert hast und sauber neu anfangen willst.

!!! warning "`--skip-if-exists` vs. der Embedding-Modell-Wächter"
    `--skip-if-exists` **prüft nur, ob die Collection existiert**. Es merkt
    nicht, dass du etwas geändert hast. Wenn du also Dokumente oder Einstellungen
    bearbeitest, überspringt diese Option die Arbeit und du bekommst weiterhin
    alte Antworten.

    Eines fängt die App aber ab: Beim ersten Durchlauf merkt sie sich, welches
    Modell deinen Text durchsuchbar gemacht hat. Wechselst du später auf ein
    anderes, verweigert sie den Dienst, statt unverträgliche Daten zu mischen.
    Nimm dann **`--recreate`** oder gib `vector_store.collection` einen neuen
    Namen.

## 5. App starten

```bash
chainlit run app.py -w
# oder der gesamte Stack (Qdrant + Postgres + Auto-Ingest + App):
make up
```

Danach <http://localhost:8000> öffnen und eine Frage stellen. Unter jeder Antwort
stehen die Quellen. Ein Klick öffnet das Originaldokument auf der richtigen
Seite.

## Doku lokal

```bash
uv run --only-group docs mkdocs serve   # http://127.0.0.1:8000
```
