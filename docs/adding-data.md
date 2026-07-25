# Adding your data

Every corpus is a `data_sources[]` entry in your config. A source declares
**where** the files are, **what format** they are, and optionally how to chunk
and tag them. You can mix several sources into one collection.

```yaml
data_sources:
  - name: handbook          # unique label (used in --only and fallback ids)
    path: ./data/handbook   # file or directory, RELATIVE TO THE CONFIG FILE
    format: pdf             # pdf | txt | md | json | csv | custom
    glob: "*.pdf"           # for directories
    chunking: {strategy: heading}   # optional per-source override
    extra_metadata: {topic: security}  # optional static metadata on every chunk
```

!!! note "Paths are relative to the config file"
    A `path` (and `pdf_options.docling_json_dir`, `sources.data_dir`, …) is
    resolved against the **directory of the YAML file**, not your shell's working
    directory. Absolute paths are used as-is. In Docker, mounted absolute paths
    (`/data/...`) or the `INGEST_DOCLING_JSON_DIR` env override apply.

## 1. Put the files somewhere

Drop your documents anywhere and point `path` at them — e.g. a `data/` folder at
the repo root:

```
pilotprojekt-rag-template/
  data/
    handbook/*.pdf
    notes/*.md
    faq.csv
  apps/chainlit/
    my-rag.yaml        # path: ../../data/handbook  (relative to this file)
```

## 2. Declare the source (by format)

=== "PDF"

    Point at a folder of PDFs. They are parsed with **Docling** (lazy import),
    which reconstructs **structured, heading-delimited sections** (with section
    titles and page ranges) via `export_to_dict()`. Enable OCR for scanned docs.

    ```yaml
    - name: handbook
      path: ../../data/handbook
      format: pdf
      glob: "*.pdf"
      chunking: {strategy: heading}        # one chunk per section
      pdf_options: {ocr: true, ocr_engine: tesseract, ocr_lang: [eng, deu]}
    ```

    **Convert once (caching):** Docling + OCR is slow, and you'll re-ingest while
    tuning the config. Pre-export the PDFs to Docling JSON once and point at that
    directory to skip live conversion on every ingest — the chunk output is the
    same, it's purely a speed optimization. Use Docling's own CLI to produce the
    JSON (one file per PDF, keeping page/provenance metadata), then let
    `pdf_options.docling_json_dir` take that fast path:

    ```bash
    docling --to json --output ../../data/handbook_json ../../data/handbook
    ```
    ```yaml
    - name: handbook
      path: ../../data/handbook_json
      format: pdf
      pdf_options: {docling_json_dir: ../../data/handbook_json}
      chunking: {strategy: passthrough}   # sections are already heading-delimited
    ```

=== "Text / Markdown"

    One section per file; good with `fixed_size` chunking.

    ```yaml
    - name: notes
      path: ../../data/notes
      format: md          # or txt
      glob: "*.md"
    ```

=== "CSV"

    One chunk per row via a [field-mapping](field-mapping.md). Use `passthrough`
    so each row stays one chunk.

    ```yaml
    - name: faq
      path: ../../data/faq.csv
      format: csv
      chunking: {strategy: passthrough}
      field_mapping:
        delimiter: ";"
        text_template: "Q: {question}\n\nA: {answer}"
        metadata: {title: question}
    ```

=== "JSON"

    Flat lists or deeply nested structures — see the full
    [Field-Mapping DSL](field-mapping.md) walkthrough.

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

    For irreducibly special structure, write a parser and reference it — see
    [Extending](extending.md).

    ```yaml
    - name: mine
      path: ../../data/mine
      format: custom
      parser_name: my_format
      chunking: {strategy: passthrough}
    ```

## 3. Choose a chunking strategy

| Strategy | What it does | Use for |
|---|---|---|
| `fixed_size` | Sliding character windows (`max_chars`, `overlap`) | Plain PDFs/text with no structure |
| `heading` | One chunk per parser section; splits only oversized sections | Heading-delimited PDFs (Docling JSON) |
| `passthrough` | Exactly one chunk per section — never split | Structured JSON/CSV records |
| `semantic` | Splits each section at embedding-similarity breakpoints; embeds sentences at ingest (extra embedding calls, not free) | Long prose without usable headings |
| `docling_hybrid` | Docling's native token-aware chunker; serializes tables/figures itself and sizes chunks by the embedding tokenizer | PDF sources only |

Set a global default under `chunking:` and override per source with a source-level
`chunking:` block.

## 4. Dry-run, then ingest

```bash
export RAG_CONFIG=my-rag.yaml
python -m kb.ingest --dry-run --only faq   # inspect text + metadata, no embedding
python -m kb.ingest                         # embed + upsert into the collection
```

!!! warning "Re-ingesting after changes"
    `--skip-if-exists` only checks that the collection exists. After changing the
    content or the `embed_model`, re-run with `--recreate` (or a new
    `vector_store.collection`) — a mismatched embedding model is refused because
    the vectors would be incompatible.

## 5. Make citations open the source file

For the "open source" side panel to work, the served files must live under
`sources.data_dir` and their extension must be allowed:

```yaml
sources:
  data_dir: ../../data/handbook
  served_extensions: [.pdf, .txt, .md]
```

Citations are built from each chunk's metadata (`source_file`/`title`/`page`).
The built-in parsers already set these; a [custom parser](extending.md) should
too. Surface extra domain fields in citations via `citation.extra_fields`.
