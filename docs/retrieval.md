# Hybrid retrieval

Semantic search is good at meaning and bad at exact strings. Ask the bundled paper
corpus for `ab15898`, an antibody catalogue number, and it hands you passages from a
different paper — to an embedding model the number is barely a signal, and passages on a
plausibly related topic exist in every one of the papers. For a corpus built on catalogue numbers,
chemical names, cell lines and proper names, that is the difference between a correct
answer and a confident wrong one.

Hybrid retrieval runs a second, purely lexical search alongside the semantic one and
merges the two rankings. Nothing else changes: the assistant still gets `top_k`
passages, and Qdrant does the merging before anything reaches the model.

## What it fixes, measured

Nine papers, ingested once. Same corpus, same embedding model, same questions —
only the query path differs:

| Query shape | Dense | Hybrid |
|---|---|---|
| a natural question containing a rare term | 76% | **93%** |
| the bare term on its own | 50% | **90%** |

Top-1 correct-document accuracy over 30 identifiers that each appear in exactly one
paper of a nine-paper corpus — catalogue numbers, cell lines, fluorophores, chemical
names. The kind of thing an embedding maps to "generic lab methods" while the exact
string is the only signal.

The failure dense produces is not a narrow one. Asked for `ab15898` it ranks
Lin 2024 first and does not return the chunk containing the number at all; hybrid puts
Schmidt 2022 on top, the paper the number is actually in. Same pattern for
`carbonylcyanide-m-chlorophenylhydrazone`: dense lands on Schmidt 2022, hybrid on
Schauer 2018. An assistant that reads the top hit therefore answers from the wrong
paper, and does so confidently, because the passage is on a plausible topic.

## Turning it on

```yaml
retrieval:
  hybrid: true
  fusion: rrf        # or dbsf
  prefetch_limit: 30 # candidates per leg before merging
```

The bundled `examples/papers` instance ships with these set, so a fresh clone runs
hybrid retrieval and can compare it against `hybrid: false` by restarting. The schema
default stays off, so an existing instance keeps its behaviour until you opt in.

Ingest writes the lexical vector into every collection it *creates* — it is a locally
computed word count and costs nothing — so for those, `hybrid` is a pure query-time
switch: flip it, restart, compare, flip it back. No re-ingest, and one collection can
serve a dense-vs-hybrid A/B. A collection created before this feature is dense-only,
and stays that way until the next ingest rebuilds it (see below) — until then,
ingesting into it keeps writing plain dense vectors rather than failing.

!!! warning "Collections from before this feature rebuild themselves once"
    A collection created before lexical vectors existed is dense-only, and its
    points cannot carry one retroactively. Ingest detects that and rebuilds it —
    no flag to remember:

    ```bash
    docker compose run --rm ingest python -m kb.ingest
    ```

    It prints why, and how many points it is discarding, because a rebuild
    re-embeds the whole corpus and that is billed gateway traffic. It fires only
    on a real defect: with `hybrid: false` a dense-only collection is perfectly
    usable and is left alone.

    **Until you run it, the app refuses to start** rather than running dense-only
    behind a config that says otherwise — it cannot ingest, so it cannot fix this
    itself. `make check` and app startup both name the same one command. The
    alternative was a silent downgrade, where hybrid appears enabled and simply
    never contributes anything.

    The same refusal covers a **lexical format change**: the tokenizer decides
    which terms are stored, so an upgrade that changes it makes existing terms
    unmatchable. The format version is recorded per collection at ingest and
    compared on every run, exactly as the embedding model is.

## The three settings

**`hybrid`** — off by default. Query-time only: two searches instead of one, then a
merge. The data underneath is the same either way.

**`fusion`** — how the two rankings become one.

- `rrf` (Reciprocal Rank Fusion) merges on *position*: being 3rd in a list is worth
  the same regardless of the score attached. Robust, and the right default when you
  do not yet know which half is carrying the search.
- `dbsf` (Distribution-Based Score Fusion) merges on *scores*, normalized across each
  list. It can beat RRF when one retriever is clearly stronger, and it can be noisier
  when scores are poorly spread. Try both and measure; do not switch on principle.

**`prefetch_limit`** — how many candidates each search contributes before merging.
It must be at least `max_top_k`, which the config loader enforces when `hybrid` is
on, because a smaller pool whose two legs return the same candidates can fuse to
fewer than `top_k` results. 30 into 5 is a reasonable start; raising it costs a
little query time and nothing else.

!!! warning "`score_threshold` only bounds the semantic half"
    A lexical match has no similarity score to compare, so `retrieval.score_threshold`
    is applied to the semantic search and not to the lexical one. With `hybrid: true`
    a chunk can therefore reach the model on the strength of one shared term even if
    its similarity is far below the threshold. If the threshold is what keeps
    off-topic questions from being answered, verify that still holds after enabling
    hybrid.

    For the same reason `verify_claim` deliberately runs a semantic-only search: it
    is the one place a score is compared against a fixed bar, and a merged score is
    a rank, not a similarity.

## What this costs

Nothing, in the sense that matters: **no model, no GPU, no new dependency.** The
lexical vector is a word count. Qdrant applies the IDF weighting server-side, so the
app never computes or stores corpus statistics. Ingest gets marginally slower;
queries do a second lookup inside the database.

## When a reranker becomes worth it

A reranker is a model that re-reads each candidate passage together with the question
and re-scores it. It is genuinely better than fusion — and genuinely expensive, since
no reranker is available on the AI gateway, so it runs locally on CPU. Expect a
multi-gigabyte download and seconds added to every query.

Do not decide this by corpus size. Decide it with a number you can measure:

!!! tip "The test: compare recall@30 with recall@5"
    - **recall@30 clearly higher than recall@5** → the right passage *is* being
      retrieved, just ranked too low. This is the gap a reranker closes, and the
      condition worth waiting for.
    - **recall@30 ≈ recall@5** → the right passage is not being found at all. A
      reranker cannot promote what was never retrieved. Fix chunking, the embedding
      model, or the lexical tokenizer instead.

Corpus scale correlates with that gap — tens of thousands of chunks, many
near-duplicate documents, or one topic dominating a heterogeneous corpus all push
recall@30 and recall@5 apart. But scale is the symptom; the gap is the signal, and
it is the one you can actually check.

Two cheaper things to try first:

1. **Source diversity.** If your top 5 passages are five chunks of the same document,
   you have a spread problem, not a ranking problem.
2. **Payload boosts.** Qdrant can re-score results with arithmetic over payload
   fields — boost tables for a table question, decay by section distance. No model,
   no inference.

If you do reach for a reranker, start with `bge-reranker-base` (~278M parameters)
rather than `bge-reranker-v2-m3` (~2.3 GB). On CPU the difference is the difference
between usable and not.

## How it works

At ingest, each chunk gets a second vector: its terms, counted. Terms are lowercased
and hyphenated compounds stay whole, so `carbonylcyanide-m-chlorophenylhydrazone` is
one term rather than three fragments, one of which is a bare `m` — that detail is most
of the win, and it lives in `apps/chainlit/kb/sparse.py` if your corpus needs different
tokenizing.

At query time the question is tokenized the same way, **minus function words**. Stored
chunks keep every word; only the question is filtered.

That exclusion is not cosmetic, and IDF does not make it unnecessary. Qdrant's
`Modifier.IDF` applies BM25's IDF term and not its other two — there is no TF
saturation and no length normalization — so a term contributes `tf × idf`, linear and
unbounded in `tf`. On the example corpus, "Was ist X und wofür wurde es verwendet?"
was won by a chunk not containing X at all: `und` occurring twelve times scored
12 × 1.67 = 20.09, against 1 × 5.47 for the rare compound that identifies the right
document. IDF weighted the terms correctly and still lost, because it bounds a term's
weight and not how often one term may count. Without the filtering, hybrid measured
*below* semantic search alone: 36% against 76%.

Both searches then run with `prefetch_limit` results each, and Qdrant fuses them down
to `top_k`. With
`hybrid: false` the query is the same single dense search as always — the lexical
vector just sits unused until you flip the switch.
