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


def _run_ingest(client, monkeypatch, texts=("BSI-Standard 200-2 gilt.",)):
    import llm
    from kb.chunkers.base import Chunk
    from kb import ingestion_pipeline as pipeline

    async def fake_embed(batch):
        return [[0.1] * 4 for _ in batch]

    monkeypatch.setattr(llm, "embed", fake_embed)
    monkeypatch.setattr(pipeline, "get_client", lambda: client)
    chunks = [Chunk(text=t, metadata={"source_file": "d.md"}, doc_id=f"d:{i}") for i, t in enumerate(texts)]
    return asyncio.run(pipeline.ingest_chunks(chunks, collection="kb", embed_model="m"))


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


def test_prefetch_limit_below_max_top_k_is_rejected():
    """A pool smaller than the largest permitted top_k can fuse to fewer than
    top_k results — silently short answers, so it fails at config load instead."""
    from pydantic import ValidationError

    from config.schema import RetrievalConfig

    with pytest.raises(ValidationError, match="prefetch_limit"):
        RetrievalConfig(top_k=5, max_top_k=50, prefetch_limit=30)
    RetrievalConfig(top_k=5, max_top_k=30, prefetch_limit=30)  # equality is legal


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
    """RRF scores peak near 1/61. A cosine-calibrated threshold on the fused
    query silently discards every result."""
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
