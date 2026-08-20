"""Retrieved chunks reach the model whole.

Every chunk used to be cut to its first 1200 characters before the model saw it,
so the lexical index matched the *stored* text while the model read a shorter copy.
`ab15898` sits at offset 2312 of its chunk: search ranked that chunk first and the
assistant still answered "finden sich keine Informationen zu ab15898" — true, because
the term had been removed on the way. `expand_context` could not recover it either;
widening adds more chunks, each cut the same way, and never revisits the one it
already had.

The bound that replaces it is `tools.max_context_chars`, applied per rendered
context by dropping whole chunks — never by cutting inside one.
"""

from __future__ import annotations

import asyncio

import pytest

import rag_tool
from rag_tool import RagResult, render_context

TERM = "ab15898"


def _chunk(text: str, source: str = "Schmidt_2022_SciReports.pdf", **meta):
    return RagResult(text=text, score=0.5, metadata={"source_file": source, **meta})


def _chunk_with_term_at(offset: int, total: int = 3434) -> str:
    """A chunk shaped like the real one: term buried past the old 1200 cut."""
    head = "Antikörper und Reagenzien wurden wie folgt bezogen. " * 60
    text = (head[:offset] + f" {TERM} " + head)[:total]
    assert text.lower().index(TERM) == offset + 1, "fixture must place the term at offset"
    return text


# --------------------------------------------------------------------------- #
# The two cases that failed in production
# --------------------------------------------------------------------------- #
def test_search_delivers_a_term_past_the_old_1200_cut(monkeypatch):
    """The reported bug: hybrid ranked the right chunk #1 and the term was still
    missing from what the model received."""
    text = _chunk_with_term_at(2312)

    async def fake_embed(texts):
        return [[0.1] * 8 for _ in texts]

    class Client:
        def query_points(self, **kw):
            point = type("P", (), {
                "payload": {"source_file": "Schmidt_2022_SciReports.pdf", "text": text},
                "score": 0.5, "vector": None,
            })()
            return type("R", (), {"points": [point]})()

    monkeypatch.setattr(rag_tool, "embed", fake_embed)
    monkeypatch.setattr(rag_tool, "_get_client", lambda: Client())

    results = asyncio.run(rag_tool.retrieve(TERM, top_k=1))
    assert TERM in results[0].text.lower(), "the term search matched must survive delivery"


def test_expand_context_delivers_the_term_too(monkeypatch):
    """The path whose whole purpose is fixing cut-off hits was itself cutting them.
    Without this, the fix could regress here while the headline case still passes."""
    text = _chunk_with_term_at(2312)

    class Client:
        def scroll(self, *a, **kw):
            point = type("P", (), {
                "payload": {"source_file": "Schmidt_2022_SciReports.pdf",
                            "text": text, "section_index": 4},
                "vector": None,
            })()
            return [point], None

    monkeypatch.setattr(rag_tool, "_get_client", lambda: Client())
    results = asyncio.run(
        rag_tool.expand_context("Schmidt_2022_SciReports.pdf", 4, window=1)
    )
    assert results, "expected the section itself"
    assert TERM in results[0].text.lower()


# --------------------------------------------------------------------------- #
# No chunk is ever cut mid-text
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("length", [1200, 1201, 3000, 6000])
def test_a_chunk_is_never_cut_mid_text(length):
    """1201 is the exact off-by-one that was silently cut. Reintroducing any
    max_len default fails every one of these."""
    body = "Adhäsion und Fibronektin. " * (length // 26 + 2)
    text = body[:length]
    context, _, kept = render_context([_chunk(text)])

    assert len(kept) == 1
    assert text in context, "the chunk must appear verbatim"
    assert "..." not in context, "an ellipsis means something was cut"


def test_whitespace_is_still_collapsed_at_retrieval():
    """Normalisation stays, and stays where it was — at retrieval, before anything
    else. `_result_key` dedups on the first 120 characters of this output, so moving
    or dropping it would silently change every dedup key."""
    assert rag_tool._normalize("ragged\n\n  text\twith   runs") == "ragged text with runs"
    assert rag_tool._normalize("  padded  ") == "padded"
    long_text = "a" * 5000
    assert rag_tool._normalize(long_text) == long_text, "must not truncate"


# --------------------------------------------------------------------------- #
# The budget drops whole chunks, and the citation numbering follows
# --------------------------------------------------------------------------- #
def test_the_budget_drops_whole_chunks_and_citations_agree(monkeypatch):
    """If the context is trimmed and the citation list is not, the model can cite
    [15] for a chunk it never received — the same two-copies-disagreeing defect."""
    monkeypatch.setattr(rag_tool.get_config().tools, "max_context_chars", 30000)
    chunks = [_chunk("x" * 10000, source=f"doc{i}.pdf") for i in range(9)]

    context, citations, kept = render_context(chunks)

    assert len(kept) == 3, "30000 budget over 10000-char chunks"
    assert len(citations.splitlines()) == 3, "citations must number exactly what was kept"
    assert "6 von 9" in context, "the notice has to name what was dropped"


def test_a_single_over_budget_chunk_is_delivered_whole_and_says_so(monkeypatch, capsys):
    """Splitting it is the bug being fixed, and returning nothing reads to the model
    as "not found". Silence was the root cause, so silence is a test failure."""
    monkeypatch.setattr(rag_tool.get_config().tools, "max_context_chars", 5000)
    text = "y" * 50000

    context, citations, kept = render_context([_chunk(text)])

    assert len(kept) == 1
    assert text in context, "delivered whole, over budget"
    assert len(citations.splitlines()) == 1
    assert "context_budget_single_chunk_over" in capsys.readouterr().out


def test_nothing_is_logged_on_the_happy_path(capsys):
    render_context([_chunk("short enough")])
    assert "context_budget" not in capsys.readouterr().out
