# When something goes wrong

Errors that have actually happened, with what causes them and what to do.

## "database disk image is malformed"

The chat history file is damaged. Your documents and the search index are separate
and are not affected, so nothing you indexed is lost.

**Why.** The chat history is a SQLite file. It used to live in the `.chainlit`
folder, which is shared between your computer and the container. SQLite needs
precise file locking to stay consistent, and Docker Desktop only imitates that
across the boundary between macOS or Windows and the Linux container. A write
interrupted at the wrong moment, for example by restarting the app, can then leave
the file broken.

**This affects Docker Desktop on macOS and Windows.** On Linux, and on Windows when
the project sits inside the WSL2 filesystem, the folder is a normal Linux filesystem
and the problem does not arise.

**Already fixed for new installs.** The database now lives in a Docker volume, which
is a proper Linux filesystem, and an existing history is moved there automatically
the first time you start the app. You should not see this again.

**If you are seeing it now**, rescue the old messages like this, from
`apps/chainlit`:

```bash
docker compose stop chainlit
mv .chainlit/chat_history.sqlite3 .chainlit/chat_history.broken
sqlite3 .chainlit/chat_history.broken ".recover" | sqlite3 .chainlit/chat_history.sqlite3
docker compose up -d
```

The third line rebuilds a healthy file from whatever is still readable. Keep the
`.broken` file until you have checked your history looks right.

## A new PDF does not show up

Put the file in `apps/chainlit/data/documents/` and wait a few seconds. The app
watches the folder and reads new files by itself.

If nothing happens, check in order:

1. **Is the app running?** `docker compose ps`
2. **What does it say?** `docker compose logs -f chainlit` and look for `[watch]`
   lines. They name every file that was added, changed or removed.
3. **Is the name unique?** Documents are recognised by file name only. A second file
   called `intro.pdf` counts as the same document, and the run warns about it.
4. **Is it the right folder?** If you use your own settings file, `data_sources[]`
   decides which folder is watched, and paths count from the folder the settings file
   is in.

See [Changing your documents](managing-documents.md).

## "the 'tesseract' binary was not found"

You switched on `ocr: true`. The supplied Docker image contains no text recognition
program, deliberately, to keep it small.

Most PDFs do not need it: they already carry real text, and the default `ocr: false`
reads them correctly. Only actual scans, where the page is a photograph, need OCR.

If yours really are scans, build your own image on top of this one and install
`tesseract-ocr` plus the languages you need. The error message spells out the exact
lines.

## The app cannot reach a model running on my own computer

Inside a container, `localhost` means the container itself, not your machine. Use
`host.docker.internal` instead:

```
LITELLM_BASE_URL=http://host.docker.internal:11434/v1
```

Remember that the model names in the settings file come from a hosted service and
will not exist on your own server, so change those too.

## Port 8000 is already in use

Something else on your machine is using it. Pick another port in `.env`:

```
APP_PORT=8080
```

The same works for `QDRANT_PORT` and `POSTGRES_PORT`. Only the port on your computer
changes, so nothing inside the app needs adjusting.

## The assistant answers, but no sources appear

Source references and follow-up questions are recognised by German wording, so they
only work with `language: de` in your settings file. Your documents themselves can be
in any language. This is a known limitation.

## Nothing here matches

The log is the fastest way to find out more:

```bash
docker compose logs --tail 100 chainlit
```

Errors while documents are being read start with `[ingest]`, and messages from the
folder watcher start with `[watch]`.
