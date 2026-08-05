# Adding your data

Each set of documents is one entry under `data_sources[]` in your settings file.
An entry says **where** the files are, **what type** they are, and optionally how
to cut them up and label them. You can list several sets and search them all
together.

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
    A `path` is counted **from the folder the settings file is in**, not from
    wherever you happen to be in the terminal. This trips people up. Paths that
    start at the root of the disk are used as written. Inside Docker, use the
    mounted paths (`/data/...`) or the `INGEST_DOCLING_JSON_DIR` setting.

## 1. Put the files somewhere

Put your documents anywhere on your machine and point `path` at them. For
example, a `data/` folder next to the project:

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

    Point at a folder of PDFs. **Docling** reads them and recognises the
    structure, so it knows the headings, which text belongs to which section, and
    which page it was on. Switch on OCR if your PDFs are scans, meaning the text
    is really a photo and cannot be selected.

    ```yaml
    - name: handbook
      path: ../../data/handbook
      format: pdf
      glob: "*.pdf"
      chunking: {strategy: heading}        # one chunk per section
      pdf_options: {ocr: true, ocr_engine: tesseract, ocr_lang: [eng, deu]}
    ```

    **Read the PDFs once and reuse the result.** Reading PDFs is slow, especially
    with OCR, and you will likely repeat it while getting your settings right. You
    can convert them once and point at that, which skips the slow step from then
    on. The result is identical, it is purely about speed:

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

    Each file becomes one section. Works well with the `fixed_size` way of
    splitting.

    ```yaml
    - name: notes
      path: ../../data/notes
      format: md          # or txt
      glob: "*.md"
    ```

=== "CSV"

    One piece per row. You describe which columns to use with a
    [field-mapping](field-mapping.md). Use `passthrough` so each row stays whole.

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

    Simple lists as well as deeply nested files. The full walkthrough is in
    [Field-Mapping DSL](field-mapping.md).

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

    If your files have an unusual structure that none of the above fits, someone
    can write a small piece of Python for it. See [Extending](extending.md).

    ```yaml
    - name: mine
      path: ../../data/mine
      format: custom
      parser_name: my_format
      chunking: {strategy: passthrough}
    ```

## 3. Choose a chunking strategy

Documents are cut into pieces before they are stored, because searching works
better on small pieces than on whole documents. There are several ways to cut:

| Strategy | What it does | Use for |
|---|---|---|
| `fixed_size` | Cuts every so many characters, with a little overlap so sentences are not lost at the seam | Plain documents with no clear structure |
| `heading` | One piece per section, splitting only sections that are too long | Documents with proper headings |
| `passthrough` | One piece per record, never split | Rows from JSON/CSV files |
| `semantic` | Cuts where the topic changes instead of at a fixed length. More accurate, but costs extra because it analyses the text while reading it in | Long flowing text without usable headings |
| `docling_hybrid` | Docling's own method. Handles tables and figures itself and sizes the pieces so they always fit the model | PDFs only |

Set one default under `chunking:` for everything, and override it for a single
set of documents with a `chunking:` block inside that entry.

## 4. Read the documents in

!!! tip "Just adding or removing a document?"
    [Changing your documents](managing-documents.md) is the short, plain-language
    version of this step. The rest of this page is about formats and chunking.

```bash
export RAG_CONFIG=my-rag.yaml
python -m kb.ingest                         # embed + upsert into the collection
python -m kb.ingest --only faq              # just one set of documents
```

A plain run keeps the collection in step with the folder. Each file is remembered
with a fingerprint of its contents, so:

- a **new** file is read in,
- an **edited** file is read again, because its fingerprint changed,
- an **unchanged** file is skipped, costing nothing,
- a **deleted** file has its entries removed, so it stops turning up in answers.

Managing your documents is therefore just managing the folder: add, replace or
delete files and run the same command again. Replacing your whole set of documents
in one go works too, removing the old entries and reading the new files in the same
run. The app also does this by itself: it watches the folders and runs the same thing
within seconds of a change, so in normal use you never call this manually. Set
`DOCUMENT_WATCH=false` to turn that off.

!!! warning "One deliberate exception"
    If the folder turns out to be **completely empty** while the collection knows
    about files, nothing is deleted. An empty folder is almost always a mount that
    did not come up or a wrong `path`, and quietly wiping the collection over that
    would be worse than doing nothing. You get a warning saying so. To empty a
    collection on purpose, use `--recreate`.

    Deleting entries needs to know which ones belong to the file. That works for
    PDF, Markdown and text sources. A `json` or `csv` source whose `field_mapping`
    writes no `source_file` cannot be matched up, so those entries are kept and
    reported, and `--recreate` clears them.

!!! danger "Give every document a unique file name"
    Documents are identified by their **file name alone**, not by which folder they
    are in. So two files both called `intro.pdf`, in different folders of the same
    collection, are the same document as far as the app is concerned: only one of
    them ends up being searchable and the other is lost. Deleting one of them is
    also refused, because its entries cannot be told apart from the other's.

    A run now warns you when it sees a repeated name. If you get that warning,
    rename the files so each is unique and read them in again with `--recreate`.

!!! warning "When you do need `--recreate`"
    Fingerprints only cover the files. If you change something that affects how
    every document is cut up or searched, the existing entries are stale and the
    whole collection has to be rebuilt:

    ```bash
    python -m kb.ingest --recreate
    ```

    That applies to a different `chunking` strategy, different chunk sizes, or
    switching `images.mode` from `none`. Switching `embed_model` is refused
    outright, because old and new vectors cannot be compared; use `--recreate` or
    a new `vector_store.collection`.

    The old `--skip-if-exists` flag still exists but is no longer useful: it stops
    the run entirely when the collection exists, which is exactly what prevents
    added, edited and deleted files from being noticed.

## 5. Make citations open the source file

For a click on a source to open the actual document, two things must be true: the
file has to sit inside the folder named in `sources.data_dir`, and its file type
has to be listed as allowed.

```yaml
sources:
  data_dir: ../../data/handbook
  served_extensions: [.pdf, .txt, .md]
```

The reference under an answer is assembled from what the app noted while reading:
file name, title and page. The built-in readers fill this in automatically. If
someone writes a [custom parser](extending.md), it should do the same. To show
additional fields of your own in a citation, list them under
`citation.extra_fields`.
