"""Hybrid retrieval: the lexical vector, and the two traps in the fused query.

Both traps fail *silently* — no exception, just empty or polluted results — so
they need assertions rather than a smoke test. See ``kb/sparse.py`` and the
hybrid branch of ``rag_tool.retrieve``.
"""

from __future__ import annotations

import asyncio

import pytest
from qdrant_client.models import Fusion

import rag_tool
from kb.sparse import SPARSE_VECTOR, sparse_vector, tokenize


# --------------------------------------------------------------------------- #
# Tokenizer — this is what decides whether the lexical leg earns its keep
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        # The whole point: a standard number must survive as one term.
        ("BSI-Standard 200-2", ["bsi-standard", "200-2"]),
        ("IFN-γ levels", ["ifn-γ", "levels"]),
        ("Verfügbarkeit und Vertraulichkeit", ["verfügbarkeit", "und", "vertraulichkeit"]),
        ("Kage et al. (2018)", ["kage", "et", "al", "2018"]),
        # Underscores split; they are identifier syntax, not compound words.
        ("source_file", ["source", "file"]),
        ("", []),
        ("...  ---  ", []),
    ],
)
def test_tokenize(text, expected):
    assert tokenize(text) == expected


# --------------------------------------------------------------------------- #
# Sparse vector
# --------------------------------------------------------------------------- #
def test_token_ids_are_stable_across_processes():
    """Ingest writes the vectors, the app queries them, and the two are separate
    processes. Anything salted per-process — Python's built-in hash() — would
    match nothing at query time while raising no error anywhere."""
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "from kb.sparse import sparse_vector; "
        "print(sparse_vector('BSI-Standard 200-2 Verfügbarkeit').indices)"
    )
    app_dir = Path(__file__).resolve().parent.parent
    runs = {
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=app_dir,
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout.strip()
        for seed in ("0", "1", "random")
    }
    assert len(runs) == 1, f"token ids differ between processes: {runs}"
    assert runs.pop() == str(sparse_vector("BSI-Standard 200-2 Verfügbarkeit").indices)


def test_repeated_terms_become_weights():
    """Term frequency is the whole payload — Qdrant applies IDF server-side."""
    vector = sparse_vector("Risiko Risiko Risiko Analyse")
    weights = dict(zip(vector.indices, vector.values))
    assert sorted(weights.values()) == [1.0, 3.0]


def test_every_index_has_exactly_one_weight():
    vector = sparse_vector("a b c a b a")
    assert len(vector.indices) == len(vector.values) == 3


def test_empty_text_yields_an_empty_vector_rather_than_raising():
    """Chunks can be whitespace or punctuation only; that must match nothing,
    not abort the ingest."""
    vector = sparse_vector("   ...   ")
    assert vector.indices == [] and vector.values == []


# --------------------------------------------------------------------------- #
# ingest — sparse is always written where the collection supports it
# --------------------------------------------------------------------------- #
class _IngestClient:
    """Minimal client for ingest_chunks: records creations and upserts."""

    def __init__(self, existing_sparse: bool | None = None):
        # None -> collection does not exist yet; bool -> exists with/without sparse
        self._sparse = existing_sparse
        self.upserted: list = []

    def get_collections(self):
        cols = [] if self._sparse is None else [type("C", (), {"name": "kb"})]
        return type("R", (), {"collections": cols})()

    def create_collection(self, collection_name, vectors_config=None, sparse_vectors_config=None):
        self._sparse = sparse_vectors_config is not None

    def get_collection(self, name):
        sparse = {"text": object()} if self._sparse else None
        params = type("P", (), {"sparse_vectors": sparse})()
        return type("I", (), {"config": type("Cfg", (), {"params": params})()})()

    def create_payload_index(self, **kw):
        pass

    def query_points(self, **kw):  # sentinel read during the model guard
        return type("R", (), {"points": []})()

    def scroll(self, *a, **kw):
        return [], None

    def upsert(self, collection_name, points):
        self.upserted.extend(points)


def _run_ingest(client, monkeypatch, texts=("BSI-Standard 200-2 gilt.",), **kw):
    import llm
    from kb.chunkers.base import Chunk
    from kb import ingestion_pipeline as pipeline

    async def fake_embed(batch):
        return [[0.1] * 4 for _ in batch]

    monkeypatch.setattr(llm, "embed", fake_embed)
    monkeypatch.setattr(pipeline, "get_client", lambda: client)
    chunks = [Chunk(text=t, metadata={"source_file": "d.md"}, doc_id=f"d:{i}") for i, t in enumerate(texts)]
    return asyncio.run(
        pipeline.ingest_chunks(chunks, collection="kb", embed_model="m", **kw)
    )


def test_new_collections_always_get_the_sparse_vector(monkeypatch):
    """`retrieval.hybrid` must be a pure query-time switch: the data written
    today has to support the flag being flipped tomorrow without a re-ingest."""
    client = _IngestClient(existing_sparse=None)
    _run_ingest(client, monkeypatch)

    assert client._sparse is True, "new collections must declare the sparse vector"
    chunk_points = [p for p in client.upserted if not (p.payload or {}).get("_meta")]
    assert chunk_points, "expected at least one chunk point"
    for point in chunk_points:
        assert isinstance(point.vector, dict) and SPARSE_VECTOR in point.vector, (
            "chunk written without its lexical vector"
        )
        assert point.vector[SPARSE_VECTOR].indices, "lexical vector must not be empty"


def test_prefetch_limit_below_max_top_k_is_rejected_only_when_hybrid_is_on():
    """A pool smaller than the largest permitted top_k can fuse to fewer than
    top_k results. But prefetch_limit is a fusion knob: enforcing it with hybrid
    off rejected configs that never read it, so `max_top_k: 50` (or MAX_TOP_K=50
    in the environment) broke every entrypoint at import."""
    from pydantic import ValidationError

    from config.schema import RetrievalConfig

    with pytest.raises(ValidationError, match="prefetch_limit"):
        RetrievalConfig(hybrid=True, top_k=5, max_top_k=50, prefetch_limit=30)
    RetrievalConfig(hybrid=True, top_k=5, max_top_k=30, prefetch_limit=30)  # equality legal
    RetrievalConfig(top_k=5, max_top_k=50)  # hybrid off: not this validator's business
    RetrievalConfig(top_k=40)  # max_top_k defaults to 40 > prefetch_limit 30


def test_ingest_chunks_uses_the_hybrid_setting_it_was_given(monkeypatch):
    """`kb.ingest --config other.yaml` loads a config that need not be the process
    singleton. Reading get_config() here meant ingest_all checked one policy and
    ingest_chunks another within a single run — the explicit argument is the only
    one that describes the corpus actually being written."""
    from kb import ingestion_pipeline

    assert rag_tool.get_config().retrieval.hybrid is False, "singleton says dense"

    ingestion_pipeline._verified_hybrid.clear()
    with pytest.raises(RuntimeError, match="no lexical vector"):
        _run_ingest(_IngestClient(existing_sparse=False), monkeypatch, hybrid=True)

    # And the same run is fine when the caller really is dense.
    ingestion_pipeline._verified_hybrid.clear()
    _run_ingest(_IngestClient(existing_sparse=False), monkeypatch, hybrid=False)


def test_legacy_dense_only_collections_keep_getting_plain_vectors(monkeypatch):
    """Qdrant rejects a vector name the collection does not declare — incremental
    ingest into a pre-sparse collection must not start failing."""
    client = _IngestClient(existing_sparse=False)
    _run_ingest(client, monkeypatch)

    chunk_points = [p for p in client.upserted if not (p.payload or {}).get("_meta")]
    for point in chunk_points:
        assert not isinstance(point.vector, dict), (
            "legacy collection got a named vector it cannot accept"
        )


# --------------------------------------------------------------------------- #
# retrieve() — the fused query
# --------------------------------------------------------------------------- #
class _Point:
    def __init__(self, payload, score):
        self.payload, self.score, self.vector = payload, score, None


class RecordingClient:
    """Captures the kwargs of every query so the call shape can be asserted."""

    def __init__(self, points=None):
        self.calls: list[dict] = []
        self._points = points or [_Point({"source_file": "a.pdf", "text": "hit"}, 0.9)]

    def query_points(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"points": list(self._points)})()


@pytest.fixture
def hybrid_client(monkeypatch):
    async def fake_embed(texts):
        return [[0.1] * 8 for _ in texts]

    monkeypatch.setattr(rag_tool, "embed", fake_embed)
    cfg = rag_tool.get_config()
    monkeypatch.setattr(cfg.retrieval, "hybrid", True)
    monkeypatch.setattr(cfg.retrieval, "prefetch_limit", 30)
    client = RecordingClient()
    monkeypatch.setattr(rag_tool, "_get_client", lambda: client)
    # The compatibility guard is a separate round trip against a real collection;
    # these tests are about the shape of the query, so let it pass.
    from kb import ingestion_pipeline

    monkeypatch.setattr(ingestion_pipeline, "verify_hybrid_compatible", lambda *a, **k: None)
    return client


def test_hybrid_sends_a_dense_and_a_sparse_leg(hybrid_client):
    asyncio.run(rag_tool.retrieve("BSI-Standard 200-2", top_k=5))

    call = hybrid_client.calls[0]
    dense, sparse = call["prefetch"]
    assert dense.using is None, "the dense vector stays unnamed"
    assert sparse.using == SPARSE_VECTOR
    assert sparse.query.indices, "the sparse leg must carry the tokenized query"
    assert call["limit"] == 5, "fusion collapses back to top_k before the model sees it"
    assert dense.limit == sparse.limit == 30, "each leg widens to prefetch_limit"


def test_score_threshold_never_reaches_the_fused_query(monkeypatch, hybrid_client):
    """A fused score is sum(1/(rank+2)) over the legs — measured against Qdrant
    1.18: 1.0 at best, 0.5 for a top hit found by one leg only. Applying a
    cosine-calibrated threshold to that filters on the wrong scale."""
    monkeypatch.setattr(rag_tool, "SCORE_THRESHOLD", 0.55)
    asyncio.run(rag_tool.retrieve("query", top_k=5))

    call = hybrid_client.calls[0]
    assert call.get("score_threshold") is None, "threshold must not apply to fused scores"
    dense, sparse = call["prefetch"]
    assert dense.score_threshold == 0.55, "it belongs on the dense leg, where scores are cosine"


def test_both_legs_exclude_the_meta_points(hybrid_client):
    """The sentinel and manifest tie with real chunks on score. Filtering only
    the outer query lets them occupy prefetch slots — the failure recorded in
    tests/test_retrieval_meta.py, one layer down."""
    asyncio.run(rag_tool.retrieve("query", top_k=2))

    for leg in hybrid_client.calls[0]["prefetch"]:
        assert leg.filter is not None, "a leg without a filter re-admits _meta points"
        assert any(c.key == "_meta" for c in leg.filter.must_not or [])


@pytest.mark.parametrize("name,expected", [("rrf", Fusion.RRF), ("dbsf", Fusion.DBSF)])
def test_fusion_strategy_comes_from_config(monkeypatch, hybrid_client, name, expected):
    monkeypatch.setattr(rag_tool.get_config().retrieval, "fusion", name)
    asyncio.run(rag_tool.retrieve("query", top_k=5))
    assert hybrid_client.calls[0]["query"].fusion == expected


def test_dense_only_collection_is_refused_with_an_actionable_message(monkeypatch):
    """Degrading quietly was worse than failing: the config and the header chip
    both keep saying hybrid is on while only dense runs. Re-ingesting is the
    intended remedy, so the error has to name it and stop."""

    async def fake_embed(texts):
        return [[0.1] * 8 for _ in texts]

    class DenseOnly(RecordingClient):
        def get_collection(self, name):
            params = type("P", (), {"sparse_vectors": None})()
            return type("I", (), {"config": type("C", (), {"params": params})()})()

        def get_collections(self):
            return type("R", (), {"collections": [type("C", (), {"name": "kb"})]})()

    monkeypatch.setattr(rag_tool, "embed", fake_embed)
    monkeypatch.setattr(rag_tool.get_config().retrieval, "hybrid", True)
    from kb import ingestion_pipeline

    monkeypatch.setattr(ingestion_pipeline, "_verified_hybrid", set())
    monkeypatch.setattr(rag_tool, "_get_client", lambda: DenseOnly())

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(rag_tool.retrieve("query", top_k=5, collection="kb"))

    message = str(excinfo.value)
    assert "kb" in message, "the error must name the collection"
    assert "--recreate" in message and "retrieval.hybrid" in message, (
        "both ways out have to be in the message — it is the whole user-facing text"
    )


# --------------------------------------------------------------------------- #
# The lexical format is stored data, so a change to it has to be caught
# --------------------------------------------------------------------------- #
class _SentinelClient:
    """Enough of a client for verify_hybrid_compatible: schema + sentinel payload."""

    def __init__(self, sparse=True, sentinel_payload=None):
        self._sparse = sparse
        self._payload = sentinel_payload

    def get_collections(self):
        return type("R", (), {"collections": [type("C", (), {"name": "kb"})]})()

    def get_collection(self, name):
        sparse = {"text": object()} if self._sparse else None
        params = type("P", (), {"sparse_vectors": sparse})()
        return type("I", (), {"config": type("C", (), {"params": params})()})()

    def retrieve(self, collection_name, ids, with_payload=False, with_vectors=False):
        if self._payload is None:
            return []
        return [type("R", (), {"payload": dict(self._payload), "vector": [0.1] * 4})()]


def _verify(client):
    from kb import ingestion_pipeline

    ingestion_pipeline._verified_hybrid.clear()
    ingestion_pipeline.verify_hybrid_compatible(client, "kb", hybrid=True)


def test_a_stale_lexical_format_is_refused():
    """The tokenizer decides the stored ids, so a collection written by a different
    version matches nothing — silently. This is the guard that turns that into an
    error; it exists because the format really did change twice mid-development."""
    from kb.sparse import SPARSE_FORMAT

    _verify(_SentinelClient(sentinel_payload={"sparse_format": SPARSE_FORMAT}))  # current: fine

    with pytest.raises(RuntimeError, match="lexical format"):
        _verify(_SentinelClient(sentinel_payload={"sparse_format": SPARSE_FORMAT + 1}))


def test_a_collection_from_before_versioning_is_tolerated():
    """No sparse_format key means it predates the guard. Matching how a missing
    embed_model is already tolerated, that must not block anyone."""
    _verify(_SentinelClient(sentinel_payload={"embed_model": "m"}))
    _verify(_SentinelClient(sentinel_payload=None))


def test_a_dense_only_collection_never_fails_the_guard_with_hybrid_off():
    """Nothing about a dense-only setup should be able to fail on a hybrid guard."""
    from kb import ingestion_pipeline

    ingestion_pipeline._verified_hybrid.clear()
    ingestion_pipeline.verify_hybrid_compatible(_SentinelClient(sparse=False), "kb", hybrid=False)


def test_a_stale_format_is_refused_even_with_hybrid_off():
    """`hybrid` is a *query-time* flag, but ingest writes lexical vectors into any
    collection whose schema has them. Skipping the format check with hybrid off let
    a run mix new-format vectors into an old-format index and then overwrite the
    recorded version, leaving a corpus no later check could tell was broken."""
    from kb import ingestion_pipeline
    from kb.sparse import SPARSE_FORMAT

    ingestion_pipeline._verified_hybrid.clear()
    with pytest.raises(RuntimeError, match="lexical format"):
        ingestion_pipeline.verify_hybrid_compatible(
            _SentinelClient(sentinel_payload={"sparse_format": SPARSE_FORMAT + 1}),
            "kb",
            hybrid=False,
        )


def test_the_sentinel_keeps_a_bare_vector_so_the_manifest_can_reuse_it():
    """_manifest_vector gates on isinstance(vector, list). If the sentinel ever
    carried the dict-shaped vector chunks use, it returns None, the manifest is
    never written, and every later ingest re-embeds the whole corpus."""
    from kb import ingestion_pipeline

    captured = []

    class Client:
        def upsert(self, collection_name, points):
            captured.extend(points)

    ingestion_pipeline._write_sentinel(Client(), "kb", "embed-model", 4, [0.1] * 4)

    point = captured[0]
    assert isinstance(point.vector, list), (
        "a dict-shaped sentinel vector silently disables incremental ingest"
    )
    assert point.payload["sparse_format"] == ingestion_pipeline.SPARSE_FORMAT


def test_verify_claim_forces_dense_so_its_floor_stays_comparable(monkeypatch):
    """verify_claim is the only caller comparing score against an absolute floor.
    Under hybrid a top hit scores 0.5 by rank alone, which clears the 0.3 floor
    regardless of similarity — the hallucination guard would pass everything."""
    seen = {}

    async def fake_retrieve(query, top_k=None, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(rag_tool, "retrieve", fake_retrieve)
    asyncio.run(rag_tool.verify_claim("a claim"))

    assert seen.get("hybrid") is False, "verify_claim must opt out of fusion"


def test_hybrid_off_keeps_the_original_single_vector_query(monkeypatch):
    """Existing instances must not change shape: one call, no prefetch, and the
    threshold back on the query itself."""

    async def fake_embed(texts):
        return [[0.1] * 8 for _ in texts]

    monkeypatch.setattr(rag_tool, "embed", fake_embed)
    monkeypatch.setattr(rag_tool.get_config().retrieval, "hybrid", False)
    monkeypatch.setattr(rag_tool, "SCORE_THRESHOLD", 0.55)
    client = RecordingClient()
    monkeypatch.setattr(rag_tool, "_get_client", lambda: client)

    asyncio.run(rag_tool.retrieve("query", top_k=5))

    call = client.calls[0]
    assert "prefetch" not in call
    assert call["score_threshold"] == 0.55
    assert call["query"] == [0.1] * 8


# --------------------------------------------------------------------------- #
# The query side strips function words; the stored side must not
# --------------------------------------------------------------------------- #
def test_the_query_vector_drops_function_words():
    """Sparse scores sum across query terms, so a question's seven common words can
    outrank the one chunk holding the rare term — and RRF then promotes that noise
    to rank 0, displacing good dense hits. Measured on natural-language questions
    wrapping 30 rare identifiers: dense 76%, hybrid without this 36%, with it 93%."""
    from kb.sparse import sparse_query_vector, sparse_vector, strip_stopwords, tokenize

    question = "Was ist carbonylcyanide-m-chlorophenylhydrazone und wofür wurde es verwendet?"
    kept = strip_stopwords(tokenize(question))

    assert "carbonylcyanide-m-chlorophenylhydrazone" in kept
    assert not {"was", "ist", "und", "wofür", "wurde", "es"} & set(kept)
    # The query vector is strictly smaller than the naive one.
    assert len(sparse_query_vector(question).indices) < len(sparse_vector(question).indices)


def test_stored_chunks_keep_every_term():
    """Stripping the stored side would change the persisted index and need a
    re-ingest, and a chunk's own function words are harmless because the query
    never asks for them."""
    from kb.sparse import sparse_query_vector, sparse_vector

    chunk = "Die Zellen wurden mit dem Puffer und der Lösung inkubiert."
    assert len(sparse_vector(chunk).indices) > len(sparse_query_vector(chunk).indices)


def test_a_question_of_only_function_words_still_searches():
    """An empty sparse vector matches nothing, which would silently turn hybrid into
    dense for that query. Falling back to the full token list is the lesser evil."""
    from kb.sparse import sparse_query_vector

    assert sparse_query_vector("Was ist das und wie?").indices, "must not be empty"


def test_the_fused_query_sends_the_stripped_sparse_vector(hybrid_client):
    """The wiring, not just the helper: retrieve() must build its sparse leg from
    the query-side vector. Using the ingest-side one halved accuracy on natural
    questions (76% dense -> 36% hybrid) by letting function words dominate."""
    from kb.sparse import sparse_query_vector, sparse_vector

    question = "Was ist ab15898 und wofür wurde es verwendet?"
    asyncio.run(rag_tool.retrieve(question, top_k=5))

    _, sparse_leg = hybrid_client.calls[0]["prefetch"]
    assert sparse_leg.query.indices == sparse_query_vector(question).indices
    assert sparse_leg.query.indices != sparse_vector(question).indices, (
        "the leg is carrying every function word in the question"
    )
