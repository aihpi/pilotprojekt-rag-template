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

### Welche Dateien eine Quelle einliest: `glob`

Zeigt `path` auf einen Ordner, entscheidet `glob`, welche Dateien darin zu dieser
Quelle gehören. Die Muster sind die von Pythons `pathlib`:

| Muster | Passt auf | Beispiel gegen die neun mitgelieferten Paper |
|---|---|---|
| `*` | beliebig viele Zeichen, auch keine | `*.pdf` nimmt alle neun |
| `?` | genau ein Zeichen | `*_202?_*.pdf` nimmt die sechs ab 2020 |
| `[seq]` | ein Zeichen aus der Menge | `Kage_20[12]*` nimmt Kage_2018 und Kage_2020 |
| `[!seq]` | ein Zeichen, das nicht in der Menge ist | `[!K]*.pdf` nimmt die sieben ohne K am Anfang |
| `**/` | in Unterordner absteigen | `**/*.pdf` |

Zwei Dinge, die Zeit kosten, wenn man sie nicht weiß:

- **Groß- und Kleinschreibung zählt.** `*.pdf` überspringt eine Datei namens
  `bericht.PDF`.
- **Klammer-Expansion gibt es nicht.** `{a,b}*.pdf` ist kein Fehler, es passt
  einfach auf nichts. Die Quelle liest dann null Dateien ein und der Lauf sieht
  erfolgreich aus. Nimm zwei Quellen oder eine Zeichenklasse.

Zeigt `path` auf eine einzelne Datei statt auf einen Ordner, wird `glob` ignoriert.

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

!!! tip "Nur ein Dokument ergänzen oder entfernen?"
    [Dokumente ändern](managing-documents.de.md) ist die kurze, allgemein
    verständliche Fassung dieses Schritts. Der Rest dieser Seite dreht sich um
    Formate und Chunking.

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
Einträge werden entfernt und die neuen Dateien eingelesen. Die App macht das auch von selbst: sie beobachtet die
Ordner und führt innerhalb von Sekunden nach einer Änderung dasselbe aus, im
Normalbetrieb rufst du das also nie manuell auf. Mit `DOCUMENT_WATCH=false`
schaltest du das ab.

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

!!! danger "Jedes Dokument braucht einen eindeutigen Dateinamen"
    Dokumente werden **allein über den Dateinamen** erkannt, nicht über den Ordner,
    in dem sie liegen. Zwei Dateien, die beide `intro.pdf` heißen und in
    verschiedenen Ordnern derselben Collection liegen, sind für die App also
    dasselbe Dokument: nur eines davon wird durchsuchbar, das andere geht verloren.
    Auch das Löschen eines der beiden wird abgelehnt, weil sich seine Einträge nicht
    von denen des anderen unterscheiden lassen.

    Ein Lauf warnt dich jetzt, wenn ein Name doppelt vorkommt. Wenn diese Warnung
    erscheint, benenne die Dateien eindeutig um und lies sie mit `--recreate` neu
    ein.

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

## 6. Eine Instanz in Teile eines Korpus aufteilen

Ein Korpus hat oft Teile, die man nicht vermischen will: Produkt-Paper, Broschüren,
interne Notizen. Du kannst sie in einer Instanz halten und die Nutzenden wählen
lassen, welcher Teil durchsucht wird.

Drei Stellen müssen zusammenpassen. Jeder Teil bekommt eine eigene Datenquelle mit
einem Etikett, das Etikett wird zum Filtern freigegeben, und eine Rolle filtert
darauf:

```yaml
data_sources:
  - name: bead-paper
    path: docs/bead-paper
    format: pdf
    extra_metadata: { kategorie: bead_paper }
  - name: flyer
    path: docs/flyer
    format: pdf
    extra_metadata: { kategorie: flyer }
    chunking: { strategy: heading }   # ein Flyer ist kein Paper

retrieval:
  payload_indexes: [kategorie]                  # Qdrant-Index für das Feld
  filterable_fields: [source_file, kategorie]   # Freigabeliste

profiles:
  - id: bead
    name: "Bead-Paper"
    retrieval_filters: { kategorie: bead_paper }
  - id: alles
    name: "Gesamter Bestand"                    # ohne Filter: sucht überall
```

`extra_metadata` wird auf jeden Chunk der Quelle kopiert, das Etikett reist also mit
dem Text mit. `filterable_fields` ist eine Freigabeliste: Ein Filter auf ein Feld, das
nicht darin steht, wird **stillschweigend ignoriert** — der übliche Grund, warum eine
Rolle scheinbar nichts tut. `payload_indexes` legt den Qdrant-Index dafür an; ohne ihn
funktioniert das Filtern weiterhin, scannt aber.

Jeder Teil kann außerdem anders gechunkt werden, und das ist oft der eigentliche
Gewinn. Ein zweiseitiger Flyer und ein zwanzigseitiges Paper wollen nicht dieselbe
Strategie.

!!! warning "Ein Filter ist kein Zugriffsrecht"
    `retrieval_filters` begrenzt, *was gesucht wird*. Wer die App benutzen kann, kann
    eine Rolle ohne Filter wählen und damit jeden Teil erreichen, und nichts in diesem
    Template vergibt oder verweigert Rechte pro Dokument. Hat ein Teil einen anderen
    Adressatenkreis als die übrigen, gib ihm eine eigene Collection und eine eigene
    Instanz. Ein Filter ist keine Grenze.

Eine Einschränkung noch: Der Assistent kann von sich aus auf ein einzelnes Dokument
einschränken (das `search`-Tool nimmt ein `document`-Argument, wenn `source_file`
freigegeben ist), aber keine Kategorie wählen. Die Kategorie kommt über die Rolle, die
der Nutzer ausgewählt hat.

Genau das in lauffähiger Form steht in
`examples/papers/rag.config.multi-source.yaml`. Die Datei teilt die neun
mitgelieferten Paper nach Erscheinungszeitraum in drei Teile, mit einer Rolle pro Teil
und einer, die alles durchsucht:

Zum Starten `RAG_CONFIG` in `apps/chainlit/.env` darauf zeigen lassen:

```bash
RAG_CONFIG=examples/papers/rag.config.multi-source.yaml
```

dann `docker compose up -d --build`. App und Ingest-Dienst lesen dieselbe Variable, ein
Eintrag schaltet also beide um, und der Ingest baut die Collection, bevor die App
startet.

Sie schreibt eine eigene Collection, liegt also neben dem kommentierten Beispiel statt
es zu ersetzen: Wer `RAG_CONFIG` zurückstellt, hat beide weiterhin.
