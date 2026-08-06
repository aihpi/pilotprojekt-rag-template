# Hybrid retrieval

Semantic search is good at meaning and bad at exact strings. Ask it for
`BSI-Standard 200-2` and it will happily hand you `200-1`, because to an embedding
model those two sentences mean almost the same thing. For a corpus built on standard
numbers, compound technical terms and proper names, that is the difference between a
correct answer and a confident wrong one.

Hybrid retrieval runs a second, purely lexical search alongside the semantic one and
merges the two rankings. Nothing else changes: the assistant still gets `top_k`
passages, and Qdrant does the merging before anything reaches the model.

## What it fixes, measured

Eight short German sentences about the BSI standards, indexed twice — once dense
only, once hybrid. Same query, same corpus, same embedding model:

| Query | Dense, rank 1 | Hybrid, rank 1 |
|---|---|---|
| `BSI-Standard 200-2` | ❌ 200-1 (0.5924) — 200-2 second at 0.5902 | ✅ 200-2 (0.8333) |
| `200-3` | ❌ 200-2 (0.4058) | ✅ 200-3 (0.8333) |

Dense got both wrong at rank 1, and in the first case the margin was 0.0022 — a coin
flip. The right document was in the results, just not first, and an assistant that
reads the top hit answers from the wrong standard.

## Turning it on

```yaml
retrieval:
  hybrid: true
  fusion: rrf        # or dbsf
  prefetch_limit: 30 # candidates per leg before merging
```

!!! warning "Existing collections need a re-ingest"
    The lexical vector is written **at ingest time**. Points indexed before you
    enabled `hybrid` do not have one, so the lexical half of the search silently
    finds nothing and you get dense behaviour with extra steps. Re-ingest with
    `--recreate`:

    ```bash
    docker compose run --rm ingest python -m kb.ingest --recreate
    ```

    Nothing warns you about this, because a collection with a half-populated
    lexical index looks perfectly healthy from the outside.

## The three settings

**`hybrid`** — off by default. Turning it on changes both ingest (a second vector per
chunk) and query (two searches, then a merge).

**`fusion`** — how the two rankings become one.

- `rrf` (Reciprocal Rank Fusion) merges on *position*: being 3rd in a list is worth
  the same regardless of the score attached. Robust, and the right default when you
  do not yet know which half is carrying the search.
- `dbsf` (Distribution-Based Score Fusion) merges on *scores*, normalized across each
  list. It can beat RRF when one retriever is clearly stronger, and it can be noisier
  when scores are poorly spread. Try both and measure; do not switch on principle.

**`prefetch_limit`** — how many candidates each search contributes before merging.
It must be larger than `top_k` or there is nothing to reorder. 30 into 5 is a
reasonable start; raising it costs a little query time and nothing else.

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
and hyphenated compounds stay whole, so `BSI-Standard` is one term rather than two
common words — that detail is most of the win, and it lives in
`apps/chainlit/kb/sparse.py` if your corpus needs different tokenizing.

At query time the question is tokenized the same way, both searches run with
`prefetch_limit` results each, and Qdrant fuses them down to `top_k`.

The dense vector stays unnamed, so a collection with `hybrid: false` is byte-identical
to one built before this feature existed.
