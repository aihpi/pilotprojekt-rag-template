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


# --------------------------------------------------------------------------- #
# Citation numbering: the alias number must be the number the model was shown
# --------------------------------------------------------------------------- #
def test_the_alias_number_is_the_retrieval_index_not_a_running_counter():
    """The model cites by the number in the tool payload's citation list, which is
    the position in last_results. A separate counter that only advanced for
    *linkable* sources drifted the moment a chunk had no resolvable PDF, so the
    answer said "Quelle 5" while the panel said "Quelle 4" — a link to the right
    document under the wrong number, and an unreliable lookup by number."""
    import app

    # Retrieval positions 2 and 5 are unlinkable; 1, 3 and 4 are fine.
    linkable = {1: ("Intro", 1, None), 3: ("Methods", 5, 6), 4: ("Results", 7, None)}
    alias_by_index = {
        idx: app._source_alias(idx, sec, start, end)
        for idx, (sec, start, end) in linkable.items()
    }

    numbers = [a.split(":")[0] for a in alias_by_index.values()]
    assert numbers == ["Quelle 1", "Quelle 3", "Quelle 4"], (
        "gaps are correct: a number that matches the context the model read is "
        "worth more than a consecutive one"
    )

    # A citation for a gap position stays as written — there is no file to link to.
    unchanged = app._normalize_source_alias_mentions("siehe Quelle 2: X (S.1)", alias_by_index)
    assert unchanged == "siehe Quelle 2: X (S.1)"

    # A citation for a linkable position is repaired to the exact alias, whatever
    # spelling the model used for the page.
    for written in ("Quelle 3: Methods (S.5)", "Quelle 3: Methoden (Seite 5-6)"):
        repaired = app._normalize_source_alias_mentions(written, alias_by_index)
        assert repaired == "Quelle 3: Methods (S.5-6)", written
