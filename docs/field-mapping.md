# Field-Mapping DSL (JSON & CSV)

For `format: json` and `format: csv` sources, a `field_mapping` turns records
into chunks: it builds each chunk's **text** and its **metadata** (which becomes
the Qdrant payload and drives citations). This page walks the DSL end-to-end,
including the nested case.

## Value grammar

Anywhere a metadata value is expected, you may write:

| Form | Meaning |
|---|---|
| `"field"` or `"a.b.c"` | Dotted lookup in the current record / binding namespace |
| `"@name"` | A key captured by `bind_key_as` |
| `{const: X}` | A literal value |
| `{template: "{a} / {b}"}` | An f-string over the namespace |

`text_template` and `id_template` are f-strings over the same namespace;
`text_fields` is a shortcut that joins several fields with blank lines.

## Flat JSON / CSV

The simplest case — one chunk per record. `record_path` selects the list
(omit it if the top level is already a list); for CSV each row is a record.

```yaml
data_sources:
  - name: faq
    path: ./data/faq.csv
    format: csv
    chunking: {strategy: passthrough}   # one chunk per row
    field_mapping:
      delimiter: ";"
      text_template: "Q: {question}\n\nA: {answer}"
      metadata:
        title: question
        category: {const: faq}
```

```yaml
  - name: articles
    path: ./data/articles.json
    format: json
    field_mapping:
      record_path: result.items       # dotted path to the list
      text_fields: [title, body]       # join these with blank lines
      id_template: "article:{id}"
      metadata:
        title: title
        author: author.name            # nested lookup
```

## Nested JSON — `record_specs` (complete walkthrough)

When records are nested several levels deep and you need to keep **ancestor
context** on each leaf, use `record_specs`. Each spec has an `iterate` list of
descent steps; ancestors bound with `as` (and keys captured with `bind_key_as`)
are available to every template and metadata value.

Consider this JSON:

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

We want one chunk per requirement, tagged with its layer, module and level:

```yaml
field_mapping:
  record_specs:
    - iterate:
        - {path: layers,  as: layer}                 # (1) iterate the list "layers"
        - {path: modules, as: module}                # (2) iterate layer.modules
        - {path: requirements, object: true}         # (3) descend into a dict (no iteration)
        - path: [basic, standard]                    # (4) iterate several sibling lists…
          as: req
          bind_key_as: level                         #     …capturing which key we came through
      text_template: "{req.id}: {req.text}"          # (5) f-string over bound names
      id_template: "req:{req.id}"
      metadata:
        layer_id:   layer.id                         # dotted lookup into a bound ancestor
        layer_name: layer.name
        module_id:  module.id
        module_title: module.title
        level:      "@level"                         # the captured sibling key
        req_id:     req.id
```

Step by step:

1. **`{path: layers, as: layer}`** — iterate the top-level list `layers`; bind
   each element as `layer`.
2. **`{path: modules, as: module}`** — for each layer, iterate `layer.modules`;
   bind each as `module`.
3. **`{path: requirements, object: true}`** — `requirements` is a **dict**, not a
   list, so `object: true` descends into it without iterating.
4. **`{path: [basic, standard], as: req, bind_key_as: level}`** — iterate *each*
   of the sibling list keys; bind each element as `req` and record which key
   (`basic`/`standard`) under `level`. A sibling key that is **absent is
   skipped**, not an error (e.g. a module with no `standard` requirements).
5. Templates and metadata reference the bound names: `{req.id}`, `layer.id`,
   `"@level"`, …

This yields two chunks (`APP.1.1.A1` at level `basic`, `APP.1.1.A5` at level
`standard`), each carrying full ancestor metadata.

## Rich error messages

Structural mistakes fail loudly. If a `record_path` or an iterate-step `path` is
missing or points at the wrong type, you get the full path, the expected vs.
actual type, and a syntax example:

```text
data source 'reqs': iterate step path 'modules' — expected a list to iterate,
but found dict. 
  Correct syntax, e.g.:  - {path: items, as: item}   (path must point at a list)
```

Field references inside `text_template`/`metadata` are lenient (missing → empty)
so optional fields don't break ingestion — validate them with `--dry-run`.

## The authoring loop

```bash
# edit field_mapping, then:
python -m kb.ingest --dry-run --only faq --limit 5
# inspect the printed text + metadata, adjust, repeat — no embeddings spent.
```
