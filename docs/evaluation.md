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

### How they are calculated

The badge shows the same thing when you click it, so you should not need this page
open while you work. It is here for reference.

**Faithfulness** breaks the answer into individual claims and checks each one
against the retrieved chunks:

```
Faithfulness = supported claims / all claims
```

That is why the number is worth more than a grade: 0.5 does not mean "mediocre", it
means *half the claims in that answer were not backed by the sources*. The
panel lists them, each with the judge's reason, so you can see which claim failed
rather than guessing.

**Relevance** generates questions from the answer and compares them to the question
that was actually asked:

```
Relevance = ⌀ cos( E(generated questionᵢ) , E(real question) )
```

`E(...)` is your embedding model, so this metric costs an embedding call as well as
a judge call.

**The badge value** is the running mean over the conversation:

```
⌀ = (1/n) · Σ valueᵢ
```

### Relevance 0% usually means the assistant declined

This one is worth knowing before it alarms you. If the answer is judged
*noncommittal* — "that is not in the documents" — the similarity is thrown away and
relevance is forced to **0**:

```
Relevance = mean(cosine) × (0 if the answer declined, else 1)
```

So 0% does not mean the answer was off-topic. It usually means the assistant refused
to answer, which is the behaviour you *want* when the corpus does not cover a
question. The panel says so explicitly when it happens.

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
  show_badge: true
```

Restart the app and ask a question. A small badge appears above the chatbox:

```
Faithfulness 67% ↗ · Relevance 88% · 3 answers
```

That is the conversation so far, not the last answer: a running average over every
answer that has been scored in this chat, with the number of answers beside it.

**The count matters.** "67% over 1 answer" and "67% over 20" are not the same
claim, which is why the badge always shows it.

**Each metric gets its own arrow, comparing the last answer to that metric's
average.** ↗ means the latest answer scored better than the conversation's average,
↘ worse, and no arrow means it landed about where the average already was. The two
move independently — `Faithfulness 58% ↘ · Relevance 17% ↗` is a normal reading, and a useful
one. An arrow needs at least two answers to mean anything, so it does not appear
before then.

**Click the badge** for the full explanation: which claims the last answer made,
which of them the sources backed, and how each number is calculated. It stays open
so you can scroll through a long list, and closes on a second click, on Escape, or
by clicking anywhere else. You should not have to remember any of this, or come back
to this page for it.

The badge belongs to one conversation, so it appears only once that conversation has
a scored answer. The start page shows nothing, rather than the numbers from whatever
you were doing last.

Badge and panel are written in German or English, following the same signal as the
rest of the interface — see
[Interface language](configuration.md#interface-language). The judge's per-claim
reasons are the exception: those come from RAGAS's English prompts and stay English
either way.

If you would rather collect the numbers without putting them in front of anyone,
set `show_badge: false`. The dashboard still fills up.

If the service is not running, the app simply records nothing. You will not see an
error and answers are unaffected.

## What it costs

Every scored answer costs **three judge calls and two embedding calls**, on top of
the answer itself: two to split the answer into claims and check them, one to
generate questions from it, and the embeddings to compare those questions with
yours. That is the whole reason this is opt-in.

It is also not fast: scoring one answer takes **roughly 15 seconds**. The two metrics
run at the same time, so that is the slower of the two rather than their sum, and
faithfulness is always the slower one.

A bigger judge model will not make it slower and a smaller one will not make it
faster. Measured across `ministral-3-14b`, `gemma-4-31b` and `llama-3-3-70b`, all
three land within a second of each other. What does drive the time is how much the
answer claims, because each claim needs a written justification.

### How it stays affordable

Faithfulness checks each claim in its own request, all at once rather than putting
every claim into one. Each claim is checked only against the handful of chunks most
likely to bear on it, chosen by word overlap, instead of against the whole retrieved
context.

Both halves matter, and the second one especially when an answer was built with
`fetch_document`, which can retrieve a whole paper. Measured on a real answer with 63
chunks, 71 kB of context and 12 claims:

| | time | input tokens |
|---|---|---|
| all claims in one request | 39.3 s | 19,244 |
| one request per claim, whole context each | 75.4 s | 226,594 |
| **one per claim, routed** | **12.8 s** | 40,181 |

Splitting without routing is worse than not splitting at all. With routing it is three
times faster than one batched request, for roughly twice the input tokens. That is
compute on a self-hosted gateway, which is what this template assumes; on a metered
API it is about twice the cost per scored answer.

The scores are unaffected. It is RAGAS's own prompt with a shorter list, and on a
deliberately mixed answer — three real claims and three invented ones — batched and
routed per-claim checking returned identical verdicts, marking exactly the same three
as unsupported.

This does not keep you waiting. Scoring starts only after the answer is on screen
and saved, so you can carry straight on asking questions. The badge updates itself
whenever a score is ready, which may be half a minute after the answer.

Because the badge belongs to the conversation rather than to any one message, a
score that finishes after you have already asked something else still counts, and
it is still there after a page reload.

If scoring seems to take many minutes rather than tens of seconds, the judge model
is probably failing rather than thinking. Check that the model you named is
actually answering.

While a judge is running the badge says `Scoring…`, so a missing number
means either that it has not started or that it failed — never that you should keep
waiting.

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
