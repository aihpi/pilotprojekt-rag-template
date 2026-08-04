# Daten hinzufügen

Jeder Satz Dokumente ist ein `data_sources[]`-Eintrag in deiner
Einstellungsdatei. Ein Eintrag sagt, **wo** die Dateien liegen, **welcher Art**
sie sind und optional, wie sie zerteilt und beschriftet werden. Du kannst mehrere
Sätze angeben und gemeinsam durchsuchen.

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
    Ein `path` zählt **ab dem Ordner, in dem die Einstellungsdatei liegt**, nicht
    ab dem Ort, an dem du gerade im Terminal stehst. Darüber stolpern viele.
    Pfade, die ganz vorne bei der Festplattenwurzel beginnen, werden unverändert
    genommen. In Docker nimmst du die gemounteten Pfade (`/data/...`) oder die
    Einstellung `INGEST_DOCLING_JSON_DIR`.

## 1. Dateien ablegen

Lege deine Dokumente irgendwo auf deinem Rechner ab und richte `path` darauf,
zum Beispiel auf einen `data/`-Ordner neben dem Projekt:

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

    Auf einen Ordner mit PDFs richten. **Docling** liest sie und erkennt dabei
    die Struktur, weiß also, wo die Überschriften sind, welcher Text zu welchem
    Abschnitt gehört und auf welcher Seite er stand. Schalte OCR ein, wenn deine
    PDFs Scans sind, der Text also in Wirklichkeit ein Foto ist und sich nicht
    markieren lässt.

    ```yaml
    - name: handbook
      path: ../../data/handbook
      format: pdf
      glob: "*.pdf"
      chunking: {strategy: heading}        # ein Chunk pro Abschnitt
      pdf_options: {ocr: true, ocr_engine: tesseract, ocr_lang: [eng, deu]}
    ```

    **PDFs einmal lesen und das Ergebnis wiederverwenden.** PDFs zu lesen ist
    langsam, mit OCR besonders, und beim Abstimmen der Einstellungen wiederholst
    du es vermutlich mehrfach. Du kannst sie einmal umwandeln und darauf zeigen,
    dann entfällt der langsame Schritt künftig. Das Ergebnis ist identisch, es
    geht rein um Geschwindigkeit:

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

    Jede Datei wird ein Abschnitt. Passt gut zur Aufteilung nach `fixed_size`.

    ```yaml
    - name: notes
      path: ../../data/notes
      format: md          # oder txt
      glob: "*.md"
    ```

=== "CSV"

    Ein Stück pro Zeile. Welche Spalten verwendet werden, beschreibst du mit
    einem [Field-Mapping](field-mapping.md). Nimm `passthrough`, damit jede Zeile
    ganz bleibt.

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

    Einfache Listen ebenso wie tief verschachtelte Dateien. Die vollständige
    Anleitung steht in der [Field-Mapping-DSL](field-mapping.md).

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

    Wenn deine Dateien eine ungewöhnliche Struktur haben, auf die nichts davon
    passt, kann jemand ein kleines Stück Python dafür schreiben. Siehe
    [Erweitern](extending.md).

    ```yaml
    - name: mine
      path: ../../data/mine
      format: custom
      parser_name: my_format
      chunking: {strategy: passthrough}
    ```

## 3. Chunking-Strategie wählen

Dokumente werden vor dem Speichern in Stücke zerteilt, weil sich kleine Stücke
besser durchsuchen lassen als ganze Dokumente. Dafür gibt es mehrere Wege:

| Strategie | Was sie tut | Wofür |
|---|---|---|
| `fixed_size` | Schneidet alle paar Zeichen, mit etwas Überlappung, damit an der Nahtstelle keine Sätze verlorengehen | Einfache Dokumente ohne klare Struktur |
| `heading` | Ein Stück pro Abschnitt, teilt nur zu lange Abschnitte | Dokumente mit ordentlichen Überschriften |
| `passthrough` | Ein Stück pro Datensatz, nie geteilt | Zeilen aus JSON/CSV-Dateien |
| `semantic` | Schneidet dort, wo das Thema wechselt, statt nach fester Länge. Genauer, kostet aber extra, weil der Text beim Einlesen analysiert wird | Lange Fließtexte ohne brauchbare Überschriften |
| `docling_hybrid` | Doclings eigene Methode. Kümmert sich selbst um Tabellen und Abbildungen und bemisst die Stücke so, dass sie immer ins Modell passen | Nur PDFs |

Setze unter `chunking:` einen Standard für alles und überschreibe ihn für einen
einzelnen Satz Dokumente mit einem `chunking:`-Block in dessen Eintrag.

## 4. Dokumente einlesen

```bash
export RAG_CONFIG=my-rag.yaml
python -m kb.ingest                         # embedden + in die Collection upserten
python -m kb.ingest --only faq              # nur ein Satz Dokumente
```

Ein normaler Lauf hält die Collection im Gleichstand mit dem Ordner. Zu jeder
Datei wird ein Fingerabdruck ihres Inhalts gespeichert, deshalb gilt:

- eine **neue** Datei wird eingelesen,
- eine **geänderte** Datei wird erneut eingelesen, weil sich der Fingerabdruck
  geändert hat,
- eine **unveränderte** Datei wird übersprungen und kostet nichts,
- eine **gelöschte** Datei verliert ihre Einträge und taucht damit nicht mehr in
  Antworten auf.

Deine Dokumente zu verwalten heißt also einfach, den Ordner zu verwalten: Dateien
hinzufügen, austauschen oder löschen und denselben Befehl noch einmal ausführen.
Auch der komplette Austausch aller Dokumente funktioniert in einem Lauf: die alten
Einträge werden entfernt und die neuen Dateien eingelesen. Genau das passiert auch
bei `docker compose up -d`, weshalb Änderungen nach einem Neustart von selbst
wirksam werden.

!!! warning "Eine bewusste Ausnahme"
    Ist der Ordner **völlig leer**, während die Collection Dateien kennt, wird
    nichts gelöscht. Ein leerer Ordner liegt fast immer daran, dass eine
    Einbindung nicht hochkam oder `path` falsch ist, und die Collection deswegen
    stillschweigend zu leeren wäre schlimmer als nichts zu tun. Du bekommst einen
    entsprechenden Hinweis. Um eine Collection absichtlich zu leeren, nimm
    `--recreate`.

    Zum Löschen muss klar sein, welche Einträge zu der Datei gehören. Bei PDF-,
    Markdown- und Textquellen ist das der Fall. Eine `json`- oder `csv`-Quelle,
    deren `field_mapping` kein `source_file` schreibt, lässt sich nicht zuordnen;
    diese Einträge bleiben erhalten und werden gemeldet, `--recreate` räumt sie
    weg.

!!! warning "Wann `--recreate` wirklich nötig ist"
    Fingerabdrücke betreffen nur die Dateien. Änderst du etwas, das sich auf die
    Zerlegung oder Suche **aller** Dokumente auswirkt, sind die vorhandenen
    Einträge veraltet und der ganze Bestand muss neu aufgebaut werden:

    ```bash
    python -m kb.ingest --recreate
    ```

    Das betrifft eine andere `chunking`-Strategie, andere Chunk-Größen oder den
    Wechsel von `images.mode: none` auf etwas anderes. Ein Wechsel des
    `embed_model` wird rundheraus abgelehnt, weil sich alte und neue Vektoren nicht
    vergleichen lassen; nimm `--recreate` oder eine neue
    `vector_store.collection`.

    Das alte `--skip-if-exists` gibt es weiterhin, nützt aber nichts mehr: es
    bricht den Lauf ab, sobald die Collection existiert, und verhindert damit genau,
    dass hinzugefügte, geänderte und gelöschte Dateien bemerkt werden.

## 5. Zitate sollen die Quelldatei öffnen

Damit ein Klick auf eine Quelle das Dokument wirklich öffnet, müssen zwei Dinge
stimmen: Die Datei muss in dem Ordner liegen, der unter `sources.data_dir` steht,
und ihr Dateityp muss als erlaubt aufgeführt sein.

```yaml
sources:
  data_dir: ../../data/handbook
  served_extensions: [.pdf, .txt, .md]
```

Die Angabe unter einer Antwort wird aus dem zusammengebaut, was sich die App beim
Lesen notiert hat: Dateiname, Titel und Seite. Die eingebauten Leseroutinen
füllen das automatisch aus. Wenn jemand einen [eigenen Parser](extending.md)
schreibt, sollte er dasselbe tun. Eigene Zusatzfelder zeigst du in Zitaten über
`citation.extra_fields` an.
