# Daten hinzufügen

Jeder Datenbestand ist ein `data_sources[]`-Eintrag in deiner Konfiguration. Eine
Quelle legt fest, **wo** die Dateien liegen, **welches Format** sie haben und
optional, wie sie gechunkt und getaggt werden. Du kannst mehrere Quellen in einer
Collection kombinieren.

```yaml
data_sources:
  - name: handbook          # eindeutiges Label (für --only und Fallback-IDs)
    path: ./data/handbook   # Datei oder Verzeichnis, RELATIV ZUR KONFIGURATIONSDATEI
    format: pdf             # pdf | txt | md | json | csv | custom
    glob: "*.pdf"           # für Verzeichnisse
    chunking: {strategy: heading}      # optionale Überschreibung pro Quelle
    extra_metadata: {topic: security}  # optionale statische Metadaten auf jedem Chunk
```

!!! note "Pfade sind relativ zur Konfigurationsdatei"
    Ein `path` (sowie `pdf_options.docling_json_dir`, `sources.data_dir`, …) wird
    relativ zum **Verzeichnis der YAML-Datei** aufgelöst, nicht relativ zum
    Arbeitsverzeichnis der Shell. Absolute Pfade werden unverändert verwendet. In
    Docker greifen gemountete absolute Pfade (`/data/...`) bzw. die
    `INGEST_DOCLING_JSON_DIR`-Umgebungs-Überschreibung.

## 1. Dateien ablegen

Lege deine Dokumente irgendwo ab und richte `path` darauf — z. B. einen
`data/`-Ordner im Repo-Root:

```
pilotprojekt-rag-template/
  data/
    handbook/*.pdf
    notes/*.md
    faq.csv
  apps/chainlit/
    my-rag.yaml        # path: ../../data/handbook  (relativ zu dieser Datei)
```

## 2. Quelle deklarieren (nach Format)

=== "PDF"

    Auf einen Ordner mit PDFs richten. Sie werden mit **Docling** geparst (Lazy
    Import), das über `export_to_dict()` **strukturierte, überschriftenbasierte
    Abschnitte** rekonstruiert (mit Abschnittstiteln und Seitenbereichen). Für
    gescannte Dokumente OCR aktivieren.

    ```yaml
    - name: handbook
      path: ../../data/handbook
      format: pdf
      glob: "*.pdf"
      chunking: {strategy: heading}        # ein Chunk pro Abschnitt
      pdf_options: {ocr: true, ocr_engine: tesseract, ocr_lang: [eng, deu]}
    ```

    **Einmal konvertieren (Caching):** Docling + OCR ist langsam, und du wirst beim
    Abstimmen der Konfiguration mehrfach ingesten. Exportiere die PDFs einmalig
    nach Docling-JSON und zeige darauf, um die Live-Konvertierung bei jedem Ingest
    zu überspringen — das Chunk-Ergebnis ist identisch, es ist reine
    Geschwindigkeitsoptimierung. Das JSON erzeugst du mit Doclings eigener CLI
    (eine Datei pro PDF, inklusive Seiten-/Provenance-Metadaten);
    `pdf_options.docling_json_dir` nutzt dann diesen Schnellpfad:

    ```bash
    docling --to json --output ../../data/handbook_json ../../data/handbook
    ```
    ```yaml
    - name: handbook
      path: ../../data/handbook_json
      format: pdf
      pdf_options: {docling_json_dir: ../../data/handbook_json}
      chunking: {strategy: passthrough}   # Abschnitte sind bereits überschriftenbasiert
    ```

=== "Text / Markdown"

    Ein Abschnitt pro Datei; gut mit `fixed_size`-Chunking.

    ```yaml
    - name: notes
      path: ../../data/notes
      format: md          # oder txt
      glob: "*.md"
    ```

=== "CSV"

    Ein Chunk pro Zeile über ein [Field-Mapping](field-mapping.md). Nutze
    `passthrough`, damit jede Zeile ein Chunk bleibt.

    ```yaml
    - name: faq
      path: ../../data/faq.csv
      format: csv
      chunking: {strategy: passthrough}
      field_mapping:
        delimiter: ";"
        text_template: "F: {question}\n\nA: {answer}"
        metadata: {title: question}
    ```

=== "JSON"

    Flache Listen oder tief verschachtelte Strukturen — siehe die vollständige
    [Field-Mapping-DSL](field-mapping.md).

    ```yaml
    - name: articles
      path: ../../data/articles.json
      format: json
      field_mapping:
        record_path: items
        text_fields: [title, body]
        metadata: {title: title}
    ```

=== "Custom"

    Für nicht reduzierbare Sonderstrukturen einen eigenen Parser schreiben und
    referenzieren — siehe [Erweitern](extending.md).

    ```yaml
    - name: mine
      path: ../../data/mine
      format: custom
      parser_name: my_format
      chunking: {strategy: passthrough}
    ```

## 3. Chunking-Strategie wählen

| Strategie | Was sie tut | Wofür |
|---|---|---|
| `fixed_size` | Gleitende Zeichenfenster (`max_chars`, `overlap`) | Einfache PDFs/Texte ohne Struktur |
| `heading` | Ein Chunk pro Parser-Abschnitt; teilt nur übergroße Abschnitte | Überschriftenbasierte PDFs (Docling-JSON) |
| `passthrough` | Genau ein Chunk pro Abschnitt — nie geteilt | Strukturierte JSON/CSV-Datensätze |
| `semantic` | Teilt jeden Abschnitt an Bruchstellen der Embedding-Ähnlichkeit; embeddet dafür Sätze beim Ingest (kostet zusätzliche Embedding-Aufrufe) | Lange Prosa ohne brauchbare Überschriften |
| `docling_hybrid` | Doclings eigener tokenbewusster Chunker; serialisiert Tabellen/Abbildungen selbst und dimensioniert Chunks über den Embedding-Tokenizer | Nur für PDF-Quellen |

Setze einen globalen Default unter `chunking:` und überschreibe pro Quelle mit
einem quellenspezifischen `chunking:`-Block.

## 4. Dry-Run, dann Ingestion

```bash
export RAG_CONFIG=my-rag.yaml
python -m kb.ingest --dry-run --only faq   # Text + Metadaten prüfen, kein Embedding
python -m kb.ingest                         # embedden + in die Collection upserten
```

!!! warning "Erneute Ingestion nach Änderungen"
    `--skip-if-exists` prüft nur, ob die Collection existiert. Nach einer Änderung
    des Inhalts oder des `embed_model` erneut mit `--recreate` ausführen (oder eine
    neue `vector_store.collection`) — ein abweichendes Embedding-Modell wird
    abgelehnt, da die Vektoren inkompatibel wären.

## 5. Zitate sollen die Quelldatei öffnen

Damit das Quellen-Seitenpanel funktioniert, müssen die ausgelieferten Dateien
unter `sources.data_dir` liegen und ihre Endung erlaubt sein:

```yaml
sources:
  data_dir: ../../data/handbook
  served_extensions: [.pdf, .txt, .md]
```

Zitate werden aus den Metadaten jedes Chunks gebaut (`source_file`/`title`/`page`).
Die eingebauten Parser setzen diese bereits; ein [eigener Parser](extending.md)
sollte das ebenfalls tun. Zusätzliche Domänenfelder erscheinen in Zitaten über
`citation.extra_fields`.
