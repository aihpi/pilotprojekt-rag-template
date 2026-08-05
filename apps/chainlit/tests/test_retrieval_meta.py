"""Metadata points must never occupy a search result slot.

Each collection holds two points that are not documents: the embed-model sentinel
and the file manifest. Qdrant wants a vector of the right size for every point, so
both are stored with the vector of the collection's first chunk. A query similar to
that chunk therefore *ties* with them on score.

Dropping them after the fact (they carry no text) is too late: they have already
taken slots inside `limit`, so the caller gets a short list, or with a small top_k
an empty one. That was reproduced live: three points tied at 0.7833 and a top_k=2
search returned nothing at all, hiding a document that had just been ingested.
"""

from __future__ import annotations

import asyncio

import pytest

import rag_tool


class _Point:
    def __init__(self, payload, score):
        self.payload = payload
        self.score = score
        self.vector = None


class FakeClient:
    """Applies just enough of the filter contract: must_not on _meta."""

    def __init__(self, points):
        self._points = points
        self.filters: list = []

    def _select(self, query_filter, limit):
        """Honour must_not on _meta; ignore the rest, which these tests do not need."""
        excluded = set()
        if query_filter is not None:
            for cond in query_filter.must_not or []:
                if cond.key == "_meta":
                    excluded.add(cond.match.value)
        kept = [p for p in self._points if p.payload.get("_meta") not in excluded]
        return type("R", (), {"points": kept[:limit]})()

    def query_points(self, collection_name, query, limit, score_threshold=None,
                     with_payload=True, with_vectors=False, query_filter=None):
        self.filters.append(query_filter)
        return self._select(query_filter, limit)


@pytest.fixture
def patched(monkeypatch):
    async def fake_embed(texts):
        return [[0.1] * 8 for _ in texts]

    monkeypatch.setattr(rag_tool, "embed", fake_embed)
    return monkeypatch


def _tie_at_top():
    """The live layout: sentinel and manifest tie with the newest chunk."""
    return [
        _Point({"_meta": True, "embed_model": "m"}, 0.7833),
        _Point({"_meta": True, "files": {"docs/a.md": "hash"}}, 0.7833),
        _Point({"source_file": "incoming.md", "text": "the new document"}, 0.7833),
        _Point({"source_file": "other.md", "text": "something else"}, 0.10),
    ]


def test_meta_points_are_excluded_in_the_query(patched, monkeypatch):
    client = FakeClient(_tie_at_top())
    monkeypatch.setattr(rag_tool, "_get_client", lambda: client)

    asyncio.run(rag_tool.retrieve("query", top_k=2))

    sent = client.filters[0]
    assert sent is not None, "a filter must be sent even without user filters"
    assert any(c.key == "_meta" for c in sent.must_not or []), "must exclude _meta"


def test_a_small_top_k_still_returns_documents(patched, monkeypatch):
    """The exact failure: two tied meta points swallowed both slots of top_k=2."""
    client = FakeClient(_tie_at_top())
    monkeypatch.setattr(rag_tool, "_get_client", lambda: client)

    results = asyncio.run(rag_tool.retrieve("query", top_k=2))

    assert len(results) == 2, "meta points must not shorten the result list"
    assert [r.metadata["source_file"] for r in results] == ["incoming.md", "other.md"]
    assert not any(r.metadata.get("_meta") for r in results)


def test_the_unfiltered_fallback_also_excludes_meta(patched, monkeypatch):
    """The retry for collections lacking the filtered fields dropped the filter."""
    # A filter on an allowed field that matches nothing forces the fallback path.
    cfg = rag_tool.get_config()
    monkeypatch.setattr(cfg.retrieval, "filterable_fields", ["source_scope"])

    class EmptyThenFull(FakeClient):
        """First query returns nothing, as an unmatched field filter would."""

        def query_points(self, collection_name, query, limit, query_filter=None, **kw):
            self.filters.append(query_filter)
            if len(self.filters) == 1:
                return type("R", (), {"points": []})()
            return self._select(query_filter, limit)

    client = EmptyThenFull(_tie_at_top())
    monkeypatch.setattr(rag_tool, "_get_client", lambda: client)

    results = asyncio.run(rag_tool.retrieve("query", top_k=2, filters={"source_scope": "nope"}))

    assert len(client.filters) == 2, "expected the fallback query to run"
    fallback = client.filters[1]
    assert fallback is not None, "the fallback must not drop the meta exclusion"
    assert any(c.key == "_meta" for c in fallback.must_not or [])
    assert not any(r.metadata.get("_meta") for r in results)
