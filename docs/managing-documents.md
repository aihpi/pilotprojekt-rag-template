# Changing your documents

Once the assistant is running, you will want to add documents, correct one, or
take one out. All of that works the same simple way:

**Change the folder, then start the app again.**

The folder decides what the assistant knows. There is nothing else to keep in
sync, and you do not need to touch any settings.

## The folder that matters

Your documents live in one folder:

```
apps/chainlit/data/documents/
```

Put files in, take files out, replace files. That is the whole interface.

## Adding a document

1. Copy the file into `apps/chainlit/data/documents/`.
2. Run this in the `apps/chainlit` folder:

```bash
docker compose up -d
```

Only the new file is read. Everything already there is left alone, so you do not
wait for it and you do not pay for it a second time.

## Correcting a document

Replace the file with the corrected version and run the same command. The app
notices that the contents changed and reads it again by itself. You do not have to
tell it anything.

Renaming a file counts as a change too: the old version is taken out and the new
name is read in.

## Removing a document

Delete the file from the folder and run the same command. Its content is removed
from the assistant, so it stops turning up in answers.

This matters more than it sounds. Without it, the assistant would keep answering
from a document you deleted, and the source link under the answer would lead
nowhere.

## Replacing everything at once

You can also swap your whole set of documents in one go: delete the old files, put
the new ones in, run the command once. The old content is removed and the new
content is read in during the same run.

## Do I have to restart anything?

No. The command above does everything, and the chat window keeps working while it
runs. You do not have to close anything, and people using the assistant do not have
to reload the page for later questions.

## How do I know it worked?

The run tells you in plain numbers. Reading in one new document out of nine looks
like this:

```
[ingest] 1 file(s) to ingest, 8 unchanged and skipped.
Ingested 49 chunks into 'papers'.
```

Removing one looks like this:

```
[ingest] removed 48 entr(ies) for deleted document data/documents/Choi_2019.pdf
```

And when there is genuinely nothing to do:

```
[ingest] nothing to do: all 9 file(s) are already indexed and unchanged.
```

Then open the chat, ask something that only the changed document can answer, and
click the source under the answer. If the right PDF opens, you are done.

## Two things to watch out for

!!! danger "Every document needs its own file name"
    Documents are recognised by their **file name**, not by the folder they sit in.
    Two files both called `intro.pdf` count as the same document, so only one of
    them is searchable and the other is lost.

    A run warns you when it spots a repeated name. Rename the files so each one is
    unique, then read everything in again (see below).

!!! warning "An empty folder is treated as a mistake"
    If the folder turns out to be completely empty, **nothing is deleted**. That is
    on purpose: an empty folder is nearly always a technical hiccup rather than
    someone meaning to throw everything away, and silently wiping the assistant's
    memory over it would be worse than doing nothing. You get a message saying so.

    To empty it deliberately, use the full rebuild below.

## When you need a full rebuild

Some changes affect **every** document, not just the ones you touched. Then
everything has to be read in again:

```bash
docker compose run --rm ingest python -m kb.ingest --recreate
docker compose up -d
```

You need this after changing how documents are cut into pieces (`chunking`), after
switching picture handling on (`images.mode`), or after changing the search model
(`embed_model`, which is refused otherwise because old and new data cannot be
compared).

!!! warning "A full rebuild costs money again"
    Every document is processed from scratch, and with `images.mode: describe` every
    picture is described again, one AI call each. The example set of nine papers has
    around 170 pictures. A normal run, which only touches what changed, is far
    cheaper.

## Where the details are

- [Adding your data](adding-data.md) covers file formats beyond PDF, and how
  documents get cut into pieces.
- [Figures & images](images.md) covers what happens with pictures and charts.
