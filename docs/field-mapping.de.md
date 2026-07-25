# Field-Mapping-DSL (JSON & CSV)

Für Quellen mit `format: json` und `format: csv` wandelt ein `field_mapping`
Datensätze in Chunks um: Es baut den **Text** jedes Chunks und seine
**Metadaten** (die zum Qdrant-Payload werden und die Zitate steuern). Diese Seite
erklärt die DSL vollständig, einschließlich des verschachtelten Falls.

## Wertgrammatik

Überall, wo ein Metadatenwert erwartet wird, kannst du schreiben:

| Form | Bedeutung |
|---|---|
| `"field"` oder `"a.b.c"` | Punktnotations-Zugriff im aktuellen Datensatz / Binding-Namensraum |
| `"@name"` | Ein durch `bind_key_as` erfasster Schlüssel |
| `{const: X}` | Ein literaler Wert |
| `{template: "{a} / {b}"}` | Ein f-String über dem Namensraum |

`text_template` und `id_template` sind f-Strings über demselben Namensraum;
`text_fields` ist eine Abkürzung, die mehrere Felder mit Leerzeilen verbindet.

## Flaches JSON / CSV

Der einfachste Fall — ein Chunk pro Datensatz. `record_path` wählt die Liste aus
(weglassen, wenn die oberste Ebene bereits eine Liste ist); bei CSV ist jede
Zeile ein Datensatz.

```yaml
data_sources:
  - name: faq
    path: ./data/faq.csv
    format: csv
    chunking: {strategy: passthrough}   # ein Chunk pro Zeile
    field_mapping:
      delimiter: ";"
      text_template: "F: {question}\n\nA: {answer}"
      metadata:
        title: question
        category: {const: faq}
```

```yaml
  - name: articles
    path: ./data/articles.json
    format: json
    field_mapping:
      record_path: result.items       # Punktpfad zur Liste
      text_fields: [title, body]       # mit Leerzeilen verbinden
      id_template: "article:{id}"
      metadata:
        title: title
        author: author.name            # verschachtelter Zugriff
```

## Verschachteltes JSON — `record_specs` (vollständiges Beispiel)

Wenn Datensätze mehrere Ebenen tief verschachtelt sind und du auf jedem Blatt den
**Kontext der Vorfahren** behalten willst, nutze `record_specs`. Jede Spec hat
eine `iterate`-Liste von Abstiegsschritten; mit `as` gebundene Vorfahren (und mit
`bind_key_as` erfasste Schlüssel) stehen jeder Vorlage und jedem Metadatenwert
zur Verfügung.

Betrachte dieses JSON:

```json
{
  "layers": [
    {
      "id": "APP", "name": "Applications",
      "modules": [
        {
          "id": "APP.1.1", "title": "Office",
          "requirements": {
            "basic":    [{"id": "APP.1.1.A1", "text": "Do X"}],
            "standard": [{"id": "APP.1.1.A5", "text": "Do Y"}]
          }
        }
      ]
    }
  ]
}
```

Wir wollen einen Chunk pro Anforderung, versehen mit Layer, Modul und Level:

```yaml
field_mapping:
  record_specs:
    - iterate:
        - {path: layers,  as: layer}                 # (1) Liste "layers" iterieren
        - {path: modules, as: module}                # (2) layer.modules iterieren
        - {path: requirements, object: true}         # (3) in ein dict absteigen (keine Iteration)
        - path: [basic, standard]                    # (4) mehrere Geschwister-Listen iterieren …
          as: req
          bind_key_as: level                         #     … und erfassen, durch welchen Schlüssel
      text_template: "{req.id}: {req.text}"          # (5) f-String über gebundenen Namen
      id_template: "req:{req.id}"
      metadata:
        layer_id:   layer.id                         # Punktzugriff in einen gebundenen Vorfahren
        layer_name: layer.name
        module_id:  module.id
        module_title: module.title
        level:      "@level"                         # der erfasste Geschwister-Schlüssel
        req_id:     req.id
```

Schritt für Schritt:

1. **`{path: layers, as: layer}`** — die oberste Liste `layers` iterieren; jedes
   Element als `layer` binden.
2. **`{path: modules, as: module}`** — je Layer `layer.modules` iterieren; jedes
   Element als `module` binden.
3. **`{path: requirements, object: true}`** — `requirements` ist ein **dict**,
   keine Liste, daher steigt `object: true` hinein, ohne zu iterieren.
4. **`{path: [basic, standard], as: req, bind_key_as: level}`** — *jeden* der
   Geschwister-Listenschlüssel iterieren; jedes Element als `req` binden und den
   Schlüssel (`basic`/`standard`) unter `level` festhalten. Ein **fehlender**
   Geschwister-Schlüssel wird übersprungen, nicht als Fehler behandelt (z. B. ein
   Modul ohne `standard`-Anforderungen).
5. Vorlagen und Metadaten referenzieren die gebundenen Namen: `{req.id}`,
   `layer.id`, `"@level"`, …

Das ergibt zwei Chunks (`APP.1.1.A1` auf Level `basic`, `APP.1.1.A5` auf Level
`standard`), jeweils mit vollständigen Vorfahren-Metadaten.

## Aussagekräftige Fehlermeldungen

Strukturelle Fehler schlagen laut fehl. Wenn ein `record_path` oder ein
Iterate-Step-`path` fehlt oder auf den falschen Typ zeigt, erhältst du den
vollständigen Pfad, den erwarteten vs. gefundenen Typ und ein Syntaxbeispiel:

```text
data source 'reqs': iterate step path 'modules' — expected a list to iterate,
but found dict. 
  Correct syntax, e.g.:  - {path: items, as: item}   (path must point at a list)
```

Feldreferenzen in `text_template`/`metadata` sind nachsichtig (fehlt → leer),
damit optionale Felder die Ingestion nicht abbrechen — validiere sie mit `--dry-run`.

## Der Autoren-Loop

```bash
# field_mapping bearbeiten, dann:
python -m kb.ingest --dry-run --only faq --limit 5
# ausgegebenen Text + Metadaten prüfen, anpassen, wiederholen — ohne Embedding-Kosten.
```
