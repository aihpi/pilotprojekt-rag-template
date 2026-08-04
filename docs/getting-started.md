# Getting Started

From nothing to a working assistant that answers questions about your own
documents. Copy each block into a terminal in the order shown.

## What you need

- **[Docker](https://docs.docker.com/get-docker/)**. On Windows it runs through
  WSL2, which Docker Desktop sets up for you.
- **Git**, to download the project. Or the *Download ZIP* button on GitHub.
- **Access to an AI service**: an address and an access key.

You do not need Python, uv, pip, Qdrant or PostgreSQL on your machine. They are
all inside the container.

!!! note "No model is included"
    The project brings the chat app, the database and the search index, but no AI
    model. It always talks to a service somewhere else, which is what the address
    and key in step 1 are for.

    Running a model server on your own machine works too, but inside a container
    `localhost` means the container itself, not your machine. Use
    `http://host.docker.internal:11434/v1` instead of
    `http://localhost:11434/v1` (11434 is the usual port for a local model
    server). You will also have to put the model names your server offers into the
    settings file, since the preset names come from a hosted service.

## 1. Get it running

```bash
git clone https://github.com/aihpi/pilotprojekt-rag-template.git
cd pilotprojekt-rag-template/apps/chainlit
cp .env.example .env
```

Open the new `.env` file and fill in the address and key of your AI service.
Nothing works until those two are set. Then start everything:

```bash
docker compose up -d --build
```

The first start takes a few minutes: it downloads the parts, reads the three
example papers, and starts the chat window. Watch it work with
`docker compose logs -f chainlit`.

Then open <http://localhost:8000> and log in with `admin` / `admin`. Ask a
question about the example papers and click a source under the answer. If the PDF
opens, everything works.

## 2. Use your own documents

Put your PDFs into `data/documents/`, then make your own settings file so the
example stays intact:

```bash
cp examples/papers/rag.config.yaml my-rag.yaml
```

Open `my-rag.yaml` and change three things:

```yaml
models:
  chat_model: gpt-oss-120b        # a model your AI service offers
  embed_model: octen-embedding-8b # a search model your AI service offers

vector_store:
  collection: my_docs             # any new name you invent
```

- The two **models** must be names your service actually offers. If you are not
  sure, ask it for its list.
- The **collection** is a name you make up. It keeps your documents separate from
  the examples. Always pick a new one, otherwise the two get mixed together.

Finally, tell the app to use your file instead of the example, by setting
`RAG_CONFIG=my-rag.yaml` in `.env`.

Every available setting is listed in the
[Configuration Reference](configuration.md).

## 3. Read your documents in

```bash
docker compose run --rm ingest python -m kb.ingest --recreate
```

This reads every document and stores it so it can be searched. Depending on how
many you have, it takes anywhere from a minute to much longer.

Use this same command whenever you add, remove or edit documents. Without it the
assistant keeps answering from the old set, and nothing warns you.

!!! warning "Describing pictures costs money"
    With `images.mode: describe` (the default in the example), every picture is
    sent to an AI model once to be described. On a large collection that adds up.
    Set `images.mode: none` if you do not need pictures to be searchable.

    If a picture cannot be described, it is left out rather than stored without a
    description. The run reports how many were affected per document, so watch for
    that line. See [Figures & images](images.md).

## 4. Restart and try it

```bash
docker compose up -d
```

Use `up -d`, not `restart`. A restart reuses the old settings, so your change to
`RAG_CONFIG` in step 2 would be ignored and the assistant would keep answering
from the example papers.

Open <http://localhost:8000> again and ask something only your documents can
answer. If you get a sensible answer with a working source link, you are done.

If the assistant says it cannot find anything, the most likely cause is that step
3 read in nothing. Check that your PDFs really are in `data/documents/` and that
`RAG_CONFIG` points at your file.

## Without Docker

Only needed if you cannot use Docker, or if you are developing on the app itself.
You need Python 3.12 or newer and a running Qdrant.

```bash
cd apps/chainlit
uv sync                                   # use uv, not pip
docker run -p 6333:6333 qdrant/qdrant     # storage for the searchable text

export RAG_CONFIG=my-rag.yaml
uv run python -m kb.ingest --recreate     # read the documents in
uv run chainlit run app.py                # http://localhost:8000
```

The `export` line has to be repeated in every new terminal window.

## Docs locally

```bash
uv run --only-group docs mkdocs serve   # http://127.0.0.1:8000
```
