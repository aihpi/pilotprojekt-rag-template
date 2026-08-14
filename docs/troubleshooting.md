# When something goes wrong

Errors that have actually happened, with what causes them and what to do.

## Start here

Most problems are one of three things: your settings, your connection, or the AI
service. This tells you which, from `apps/chainlit`:

```bash
make check
```

It tries every model a few times and reports each one. Anything other than all
green is explained below.

## Some calls work and others fail

The check reports something like `only 3 of 5 attempts worked`, or reading documents
produces many `Connection error` messages while a few figures succeed.

That pattern means your settings are fine. A wrong address or key fails every time,
not sometimes. The connection between you and the service is dropping requests.

Reading documents is where this hurts, because it makes hundreds of calls: a
connection that loses one request in three will fail dozens of times, while a single
chat message may work fine and hide the problem.

What to try, in order:

1. **Turn the VPN off**, if you use one. It is the most common cause.
2. **Use a different network.** A busy shared network in a full room does this.
3. **Wait and retry.** The service itself may be overloaded.
4. **Read documents in without pictures first**, by setting `images.mode: none`. That
   removes most of the calls, so you can confirm everything else works before paying
   for figure descriptions.

## "database disk image is malformed"

The file holding your **chat history** got damaged. Nothing else is affected: your
documents and everything the assistant learned from them are stored separately.

**Why it happened.** That file used to be kept in a folder shared between your
computer and the app. Sharing a folder like that is convenient, but this kind of file
does not tolerate it: if the app is stopped at exactly the wrong moment while it is
writing, the file can break. Restarting the app repeatedly makes it more likely.

**Who it affects.** Only Mac and Windows. On Linux the shared folder behaves
differently and the problem cannot happen.

**Already fixed.** The file has been moved somewhere the app has to itself, and your
existing history was carried over automatically. You should not see this again.

**If you are seeing it now**, rescue the old messages like this, from
`apps/chainlit`:

```bash
docker compose stop chainlit
mv .chainlit/chat_history.sqlite3 .chainlit/chat_history.broken
sqlite3 .chainlit/chat_history.broken ".recover" | sqlite3 .chainlit/chat_history.sqlite3
docker compose up -d
```

The third line builds a healthy file out of everything that is still readable. Keep
the `.broken` file until you have checked that your history looks right.

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

You switched on `ocr: true`, which reads text out of pages that are really pictures.
The program that does that is not included, on purpose, to keep the download small.

Most PDFs do not need it. A normal PDF already contains real text you can select and
copy, and the default setting reads those correctly. You only need this for scans,
where somebody photographed or scanned a paper page.

If your documents really are scans, the error message prints the exact three lines to
add so the program gets installed.

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

## Reading documents stopped with an error

The run now ends with an explanation rather than a wall of text: what the error was,
what it means, and numbered steps. It also names `make check`, which tests your
connection and settings on their own.

One thing to know: documents indexed by earlier runs are safe, but whatever was being
read when it stopped was not saved. Running it again reads those files once more.

## The score badge does not appear

The badge above the chatbox shows how well each answer scored. If it never appears,
check these in order:

1. **Is evaluation turned on?** It is off by default. Your settings file needs
   `evaluation: {enabled: true}` and the evaluation service must be running
   (`docker compose --profile eval up -d`).
2. **Did you ask a question that uses the knowledge base?** Evaluation only scores
   answers that looked something up. If the assistant answered from general
   knowledge or said "not in the documents", there is nothing to score.
3. **Wait about fifteen seconds.** Scoring happens in the background after the
   answer is already shown. The badge appears once the score arrives.

If none of that helps, check the app log for lines starting with `[WARN]
eval_status_unavailable` — that means the app cannot reach the evaluation service.

## Scores are all empty (NULL)

The badge appears but the numbers are missing. This means the scoring model (the
*judge*) tried to grade the answer but failed.

The most common cause is that the judge model is not available at the AI service
right now. Check:

```bash
docker compose logs --tail 20 eval
```

If you see `500 Internal Server Error` or `Connection error`, the judge model is
down at the service. Wait and try again, or point `evaluation.judge_model` in your
settings file at a different model that your service offers.

A failed score is stored as empty, never as zero. That way a temporary outage does
not drag the averages down.

## The badge shows numbers I do not trust

Two things to check:

**Are you comparing across different models?** If `evaluation.judge_model` is not
set, the model that *answered* also grades its own answer. That makes scores
unreliable when you switch models in the settings panel, because each model judges
itself differently. Set `judge_model` to a fixed model that is not the one being
judged.

**What do the metrics actually measure?**

- **Faithfulness** checks whether the answer's claims are supported by the text
  passages that were retrieved. It breaks the answer into individual statements,
  then checks each one against the sources. The formula is simply: supported
  claims divided by all claims. A score of 0.5 does not mean "mediocre" — it means
  half the statements in that answer were not backed by the sources. The badge
  panel lists every claim with a tick or cross and the judge's reason, so you can
  see exactly which one failed.

- **Relevance** checks whether the answer actually addresses the question. It
  generates questions from the answer and compares them with the original question
  using embedding similarity. If the generated questions are close to the real one,
  the answer is relevant. A relevance of 0% usually does not mean the answer was
  off-topic — it typically means the assistant declined to answer ("not in the
  documents"), which is correct behaviour when the knowledge base does not cover
  the question.

Both scores come from the [RAGAS](https://docs.ragas.io/) evaluation framework.
They are *reference-free*: no hand-written correct answers are needed. The trade-off
is that they measure what the judge model *thinks*, which carries its own opinions
and noise. Compare changes between runs ("did this config change help?"), do not
read a single number as a verdict.

## Nothing here matches

The log is the fastest way to find out more:

```bash
docker compose logs --tail 100 chainlit
```

Errors while documents are being read start with `[ingest]`, and messages from the
folder watcher start with `[watch]`.
