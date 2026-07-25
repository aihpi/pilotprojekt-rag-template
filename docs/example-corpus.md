# Example Corpus

The template ships a small corpus so it runs immediately after cloning — no data
hunting, no config writing. You get a working chatbot first, then swap in your own
documents.

## What is included

Three open-access articles in `apps/chainlit/data/documents/`, all published in
*Scientific Reports* under **CC BY 4.0**:

| File | Article | Reference |
|---|---|---|
| `Kage_2018_SciReports.pdf` | Luminescence lifetime encoding in time-domain flow cytometry | *Sci Rep* **8**, 16715 (2018), DOI [10.1038/s41598-018-35137-5](https://doi.org/10.1038/s41598-018-35137-5) |
| `Schmidt_2022_SciReports.pdf` | A multiparametric fluorescence assay for screening aptamer–protein interactions based on microbeads | *Sci Rep* **12**, 2961 (2022), DOI [10.1038/s41598-022-06817-0](https://doi.org/10.1038/s41598-022-06817-0) |
| `Lin_2024_SciReports.pdf` | Coupling a recurrent neural network to SPAD TCSPC systems for real-time fluorescence lifetime imaging | *Sci Rep* **14**, 3286 (2024), DOI [10.1038/s41598-024-52966-9](https://doi.org/10.1038/s41598-024-52966-9) |

The full attribution (titles, authors, licence links) lives in
`apps/chainlit/data/documents/SOURCES.md` — that file is the authoritative
credit, so it is not duplicated here.

## The matching instance

The corpus belongs to `examples/papers/rag.config.yaml` (collection `papers`),
the reference instance with every feature switched on:

- five agentic [tools](tools.md) (`search`, `list_documents`, `fetch_document`,
  `expand_context`, `verify_claim`);
- `chunking.strategy: semantic` — splits at embedding-similarity breakpoints;
- `images.mode: describe` with `inline_figures` — figures get searchable
  descriptions and are shown above the paragraph that cites them (see
  [Figures & Images](images.md)).

Both `.env.example` and `docker-compose.yml` already point at it, so `make up`
brings up this instance unchanged.

**Why these papers:** they contain real tables and figures. That makes them a
useful demonstration of table serialization, figure descriptions, and
multi-document retrieval — things a plain prose corpus would not exercise.

## Using your own corpus

1. **Drop your PDFs into `apps/chainlit/data/documents/`.** Your own files are
   gitignored (only the three examples sit on an allowlist), so they never show up
   in `git status`. You may delete the examples — no code depends on them.

    ```bash
    cp ~/my-papers/*.pdf apps/chainlit/data/documents/
    ```

2. **Copy the config and give it a fresh collection.** Changing
   `vector_store.collection` is **required** — otherwise your new vectors mix with
   the old ones in the same collection. Adjust
   `prompt.starter_questions` while you are there, since the shipped ones name the
   example papers.

    ```bash
    cp examples/papers/rag.config.yaml my-rag.yaml
    ```
    ```yaml
    vector_store:
      collection: my_papers        # new name — do not reuse `papers`

    prompt:
      starter_questions:
        - "Which documents are in the knowledge base?"
    ```

3. **Dry-run, then ingest.** `--dry-run` parses and chunks without embedding, so
   you can check the output before paying for embeddings.

    ```bash
    RAG_CONFIG=my-rag.yaml python -m kb.ingest --dry-run
    RAG_CONFIG=my-rag.yaml python -m kb.ingest             # or --recreate
    ```

Then start the app as usual. For format options, chunking strategies, and
citation wiring see [Adding your data](adding-data.md); for the full first-run
walkthrough see [Getting Started](getting-started.md).

!!! note "Switching the embedding model"
    A collection records the embedding model it was built with. If you change
    `models.embed_model`, re-ingest with `--recreate` or use a new collection — the
    app refuses a collection whose embedding model differs, because the vectors
    would be incompatible.
