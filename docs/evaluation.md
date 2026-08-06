# Checking answer quality

You can change the chunking strategy, the embedding model or the chat model in
`rag.config.yaml`. The hard part is knowing whether the change helped.

Evaluation gives every answer two scores and collects them per configuration, so
"is this better?" becomes something you can look at instead of something you argue
about.

It is **off by default**. Nothing is measured, sent or stored until you switch it
on.

## The two numbers

**Faithfulness** asks whether the claims in the answer are actually supported by
the text chunks that were retrieved. A low score means the assistant said things
its sources do not back up.

**Relevance** asks whether the answer addresses the question that was asked. A low
score means the answer may be perfectly true and still beside the point.

Neither one needs a "correct answer" written by hand, which is why they work on
the real conversations people are already having.

## Read the change, not the number

This is the one thing worth remembering.

A faithfulness of 0.87 on its own tells you almost nothing. There is no threshold
where an answer becomes good. Both numbers come from a language model judging
another language model, so they carry its opinions and its noise.

What does tell you something:

```
faithfulness 0.87  ->  0.71   after switching chunking to `heading`
```

That is a signal. Compare runs against each other, and be suspicious of any
conclusion drawn from a single number.

## Switching it on

Two things have to be true.

**1. Start the evaluation service.** It runs as its own container and is not part
of the normal startup:

```bash
docker compose --profile eval up -d
```

**2. Turn it on in your config.** In your `rag.config.yaml`:

```yaml
evaluation:
  enabled: true
  metrics: [faithfulness, relevance]
  judge_model: null      # null uses models.chat_model
  show_inline: true
```

Restart the app and ask a question. The scores appear under the answer:

```
Faithfulness: 92% · Relevance: 87%
```

If you only want the numbers collected for the dashboard, without them appearing
under every answer, set `show_inline: false`.

If the service is not running, the app simply records nothing. You will not see an
error and answers are unaffected.

## What it costs

Every scored answer costs **two judge calls and one embedding call**, on top of
the answer itself. That is the whole reason this is opt-in.

It is also not fast. Measured against a self-hosted 70B model that was answering
ordinary requests in about a second, scoring one answer took **roughly 40 seconds**.
The two metrics run at the same time, so that is the slower of the two rather than
the sum, but it is still far longer than the answer took.

This does not keep you waiting. Scoring starts only after the answer is on screen
and saved, so you can carry straight on asking questions. The scores appear
underneath the answer whenever they are ready, which may be half a minute later.

If scoring seems to take many minutes rather than tens of seconds, the judge model
is probably failing rather than thinking. Check that the model you named is
actually answering.

The judge calls go through the same gateway and the same credentials as everything
else, so there is nothing extra to configure.

Point `judge_model` at a **different** model from the one being judged if you can.
A model grading its own work tends to be generous with itself.

## The dashboard

With the service running, the dashboard is at **<http://localhost:8001>**.

There is a link in the app header for it, but it is commented out by default so
that people who never turn evaluation on do not get a link that goes nowhere. To
enable it, uncomment the `[[UI.header_links]]` block for Evaluation in
`apps/chainlit/.chainlit/config.toml`.

The table shows one row per configuration:

| Column | Meaning |
|---|---|
| Configuration | Chat model, embedding model, chunking, and collection |
| Answers | How many answers were scored under it |
| Faithfulness | Average across those answers |
| Relevance | Average across those answers |
| Thumbs | How often people clicked helpful or not helpful |

Watch the answer count. "0.91 across 3 answers" and "0.91 across 300" are not the
same claim.

An answer whose judge call failed still counts in `Answers`, but contributes no
score. A judge error is not evidence of a bad answer, so it is left out of the
average rather than counted as a zero.

## Comparing two configurations

Scores are grouped by chat model, embedding model, chunking strategy, chunk size
**and collection**. Two configurations that differ in any of those appear as
separate rows.

So to compare them properly, give each one its own collection and ingest into
both. If you point two different chunking strategies at the same collection, the
second ingest overwrites the first, and older rows in the dashboard end up
describing a corpus that is no longer there.

A practical run:

1. Ingest with strategy A into collection `papers_a`.
2. Ask your ten usual questions.
3. Ingest with strategy B into collection `papers_b`.
4. Ask the same ten questions.
5. Compare the two rows.

Asking the *same* questions matters. Different questions produce different scores
regardless of configuration.

## When someone clicks "not helpful"

Chainlit already offers a comment box with the thumbs buttons. If a comment is
left on a thumbs down, it gets sorted into one of four categories, which is also
a hint about where to look:

| Category | Meaning | Where to look |
|---|---|---|
| `hallucination` | The answer claimed something the documents do not support | The system prompt, and the faithfulness scores |
| `wrong_document` | The wrong source was retrieved or cited | Chunking strategy and embedding model |
| `incomplete` | Correct, but key information was missing | `retrieval.top_k` and chunk size |
| `irrelevant` | The answer did not address the question | How questions reach retrieval |

The original comment is always kept, so you can read what someone actually wrote.
If the classification is unclear, the comment is stored without a category rather
than being forced into the nearest one.

Thumbs up is never classified. It is not a failure, and running it through a
failure list would invent one.

## What this does not tell you

- **Whether an answer is useful.** A faithful, relevant answer can still be
  unhelpful. Nothing here replaces reading a few answers yourself.
- **Whether retrieval missed something.** Both metrics only look at chunks that
  were actually retrieved. If the right passage was never found, neither number
  drops. Measuring that needs a written correct answer per question, which this
  does not ask you for.
- **Anything absolute.** Worth repeating, because the numbers invite it.

## Where it runs

Scoring happens in a separate service, not in the app. The app sends the question,
the answer and the retrieved chunks after the answer is already on screen, then
adds the scores to it. So a slow judge never delays an answer, and with evaluation
off the app behaves exactly as it would without any of this.

The scores live in their own SQLite database in the `eval_db` volume, separate from
your chat history.
