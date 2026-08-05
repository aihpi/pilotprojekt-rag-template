# Changing your documents

Once the assistant is running, you will want to add documents, correct one, or
take one out. All of that works the same simple way:

**Change the folder. That is all.**

The app keeps an eye on your document folder and picks up anything you add, change
or delete within a few seconds, on its own. You do not have to run a command, and
you do not have to restart anything.

## The folder that matters

Your documents live in one folder:

```
apps/chainlit/data/documents/
```

Put files in, take files out, replace files. That is the whole interface.

## Adding a document

Copy the file into `apps/chainlit/data/documents/`. Within a few seconds it is read
in and ready to be asked about.

Only the new file is read. Everything already there is left alone, so you do not
wait for it and you do not pay for it a second time.

A big file being copied in is left alone until the copy has finished, so it never
gets read half-complete.

## Correcting a document

Replace the file with the corrected version. The app notices that the contents
changed and reads it again by itself. You do not have to tell it anything.

Renaming a file counts as a change too: the old version is taken out and the new
name is read in.

## Removing a document

Delete the file from the folder. Its content is removed from the assistant, so it
stops turning up in answers.

This matters more than it sounds. Without it, the assistant would keep answering
from a document you deleted, and the source link under the answer would lead
nowhere.

### What stays behind, on purpose

Deleting the PDF stops it being used, but it does **not** delete the pictures that
were extracted from it, or the descriptions written for those pictures. They stay
in `figures/` and `descriptions/`.

That is deliberate. Describing pictures costs money, so leaving them means you can
take a document out to compare answers without it, put it back later, or point a
second collection at the same documents, without paying for any of that again.

It also means the pictures are still on disk after you delete the document, and
anyone who can log in can still open them. **If you need a document gone
completely, delete all three:**

```bash
cd apps/chainlit/data/documents
rm Kage_2018_SciReports.pdf              # the document
rm -rf descriptions/Kage_2018_SciReports # what the AI wrote about its pictures
rm figures/Kage_2018_SciReports__fig*    # the pictures themselves
```

Deleting only the descriptions is useful on its own if you want them written
fresh, after changing the prompt or the image model for example. They come back on
the next **full rebuild**, not on the next automatic update, because a document
whose content has not changed is skipped. See
[When you need a full rebuild](#when-you-need-a-full-rebuild).

## Replacing everything at once

You can also swap your whole set of documents in one go: delete the old files and
put the new ones in. The old content is removed and the new content is read in
together.

## Do I have to run or restart anything?

No, neither. The chat window keeps working the whole time, and people using the
assistant do not have to reload the page for later questions.

If you would rather do it yourself, you can. Put `DOCUMENT_WATCH=false` in your
`.env` file and nothing is picked up automatically; then this reads everything that
changed, whenever you choose:

```bash
docker compose up -d
```

## How do I know it worked?

**In the chat window.** A small marker sits in the bottom right corner. It is faint
and stays out of your way; hover over it to read what it is doing.

- a quiet dot means nothing is going on, and hovering shows the last change
- a turning circle with a short text means it is working right now
- a green tick means it has finished

**In the log**, if you want the detail:

```bash
docker compose logs -f chainlit
```

Adding a document looks like this:

```
[watch] documents changed (new: 1, edited: 0, removed: 0); indexing
[watch]   new: data/documents/Choi_2019.pdf
[watch] done: 48 chunk(s) indexed
```

Removing one looks like this:

```
[watch] documents changed (new: 0, edited: 0, removed: 1); indexing
[watch] done: 49 entr(ies) removed
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

    Repeated names also make picture descriptions overwrite each other, so both
    documents have their pictures described again on every run. Renaming fixes that
    too.

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
