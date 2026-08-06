"""Chunk boundaries, oversize guards, and the schema/registry contract.

Chunking decides what retrieval can ever return: a boundary bug shows up much
later as "the answer was cut off" and is near-impossible to trace back from an
end-to-end score. Only ``semantic`` touches the network, and its ``embed_sync``
is patched here.
"""

from __future__ import annotations

import string
from typing import get_args

import pytest
from pydantic import ValidationError

import llm
from config.schema import ChunkingConfig
from kb.chunkers import CHUNKER_REGISTRY, get_chunker
from kb.chunkers.docling_hybrid import chunk_docling_hybrid
from kb.chunkers.fixed_size import chunk_fixed_size, window_text
from kb.chunkers.heading import chunk_heading
from kb.chunkers.passthrough import chunk_passthrough
from kb.chunkers.semantic import chunk_semantic
from kb.parsers.base import Section

ALPHABET = string.ascii_lowercase * 8


def _cfg(**kw) -> ChunkingConfig:
    return ChunkingConfig(**{"max_chars": 40, "overlap": 10, **kw})


def _section(text: str, **metadata) -> Section:
    return Section(text=text, metadata=metadata or {"source_file": "d.pdf"}, doc_id="d")


# --------------------------------------------------------------------------- #
# Registry / schema agreement
# --------------------------------------------------------------------------- #
def test_every_declared_strategy_is_registered():
    """The schema's Literal is the user-facing list. A value that passes config
    validation but has no chunker fails at ingest, after the parse work is done."""
    declared = set(get_args(ChunkingConfig.model_fields["strategy"].annotation))
    assert declared == set(CHUNKER_REGISTRY), (
        f"declared but unregistered: {declared - set(CHUNKER_REGISTRY)}; "
        f"registered but undeclared: {set(CHUNKER_REGISTRY) - declared}"
    )


def test_unknown_strategy_names_the_registered_ones():
    with pytest.raises(KeyError) as excinfo:
        get_chunker("sematic")
    message = str(excinfo.value)
    assert "sematic" in message
    for name in CHUNKER_REGISTRY:
        assert name in message


# --------------------------------------------------------------------------- #
# window_text — the sliding window everything else falls back to
# --------------------------------------------------------------------------- #
def test_overlap_at_or_above_max_chars_is_rejected_by_the_config():
    """window_text has no internal progress guard: with overlap >= max_chars the
    window start never advances and ingest hangs. This validator is the only
    thing preventing it, so it is load-bearing."""
    for overlap in (100, 150):
        with pytest.raises(ValidationError):
            ChunkingConfig(max_chars=100, overlap=overlap)

    ChunkingConfig(max_chars=100, overlap=99)  # the largest legal overlap


def test_short_text_yields_one_normalized_window():
    windows = list(window_text("  spaced   out\n\ttext  ", max_chars=40, overlap=10))
    assert windows == ["spaced out text"]


def test_consecutive_windows_share_exactly_the_overlap():
    windows = list(window_text(ALPHABET[:100], max_chars=40, overlap=10))

    assert all(len(w) <= 40 for w in windows)
    for earlier, later in zip(windows, windows[1:]):
        assert earlier[-10:] == later[:10]
    # Every character survives somewhere, in order.
    assert windows[0] + "".join(w[10:] for w in windows[1:]) == ALPHABET[:100]


def test_no_empty_trailing_window_when_the_text_ends_on_a_boundary():
    """Text whose last window lands exactly on len(text) must not emit a final
    empty chunk — an empty chunk gets embedded and pollutes retrieval."""
    for length in (40, 70, 100):
        windows = list(window_text(ALPHABET[:length], max_chars=40, overlap=10))
        assert all(w for w in windows), f"empty window at length {length}"


def test_fixed_size_numbers_chunks_from_one_per_section():
    sections = [_section(ALPHABET[:100]), _section("short")]
    chunks = chunk_fixed_size(sections, _cfg())

    assert [c.metadata["chunk_index"] for c in chunks] == [1, 2, 3, 1]
    assert [c.doc_id for c in chunks] == ["d:c1", "d:c2", "d:c3", "d:c1"]
    assert all(c.metadata["source_file"] == "d.pdf" for c in chunks)


# --------------------------------------------------------------------------- #
# heading — passthrough with an oversize guard at 2 * max_chars
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "length,expect_split",
    [(79, False), (80, False), (81, True)],  # the guard is `>`, not `>=`
)
def test_heading_splits_only_past_twice_max_chars(length, expect_split):
    chunks = chunk_heading([_section(ALPHABET[:length])], _cfg(max_chars=40, overlap=10))

    if expect_split:
        assert len(chunks) > 1
    else:
        assert len(chunks) == 1
        assert chunks[0].text == ALPHABET[:length], "an unsplit section must pass through verbatim"


def test_heading_falls_back_to_a_positional_doc_id():
    sections = [Section(text="a", metadata={}), Section(text="b", metadata={})]
    assert [c.doc_id for c in chunk_heading(sections, _cfg())] == ["section:1", "section:2"]


# --------------------------------------------------------------------------- #
# passthrough / docling_hybrid — one chunk per section, always
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "chunker,prefix", [(chunk_passthrough, "section"), (chunk_docling_hybrid, "chunk")]
)
def test_passthrough_chunkers_never_split(chunker, prefix):
    """A CSV row or a Docling hybrid chunk is already the retrieval unit —
    splitting it would cut a record in half."""
    huge = _section(ALPHABET * 10)
    chunks = chunker([huge], _cfg(max_chars=40, overlap=10))

    assert len(chunks) == 1
    assert chunks[0].text == huge.text

    plain = chunker([Section(text="x", metadata={})], _cfg())
    assert plain[0].doc_id == f"{prefix}:1"


@pytest.mark.parametrize("chunker", [chunk_passthrough, chunk_docling_hybrid, chunk_heading])
def test_chunk_metadata_is_copied_not_aliased(chunker):
    """Chunks get per-chunk keys written into them downstream; sharing the dict
    would leak one chunk's metadata onto every sibling from the same section."""
    section = _section("text", source_file="d.pdf")
    chunk = chunker([section], _cfg())[0]

    chunk.metadata["page"] = 7
    assert "page" not in section.metadata


# --------------------------------------------------------------------------- #
# semantic — breakpoints from sentence embeddings
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_embed(monkeypatch):
    """Map each sentence to a unit vector by its first character: 'A...' -> [1,0],
    anything else -> [0,1]. Distance is 0 within a group and 1 across."""
    calls: list[list[str]] = []

    def embed_sync(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] if t.startswith("A") else [0.0, 1.0] for t in texts]

    monkeypatch.setattr(llm, "embed_sync", embed_sync)
    return calls


def test_semantic_breaks_where_the_distance_spikes(fake_embed):
    text = "Alpha one. Also two. Beta three. Because four."
    chunks = chunk_semantic([_section(text)], _cfg(max_chars=500, overlap=10))

    assert [c.text for c in chunks] == ["Alpha one. Also two.", "Beta three. Because four."]
    assert [c.metadata["chunk_index"] for c in chunks] == [1, 2]
    assert [c.doc_id for c in chunks] == ["d:c1", "d:c2"]


def test_semantic_keeps_a_uniform_section_whole(fake_embed):
    """No distance exceeds the percentile, so there is no breakpoint at all."""
    text = "Alpha one. Also two. Always three."
    chunks = chunk_semantic([_section(text)], _cfg(max_chars=500, overlap=10))

    assert len(chunks) == 1
    assert chunks[0].text == text


def test_semantic_skips_embedding_a_single_sentence(fake_embed):
    """Embedding runs per sentence at ingest and costs real money — the
    short-circuit for a section that cannot be split must stay."""
    chunks = chunk_semantic([_section("Only one sentence here.")], _cfg(max_chars=500))

    assert fake_embed == [], "embed_sync was called for a section with nothing to split"
    assert len(chunks) == 1


def test_semantic_keeps_a_blank_line_block_intact(fake_embed):
    """A serialized table has no sentence punctuation; splitting on newlines
    would shred it into unretrievable fragments."""
    table = "| a | b |\n| 1 | 2 |\n| 3 | 4 |"
    section = _section(f"Alpha intro.\n\n{table}")
    chunk_semantic([section], _cfg(max_chars=500, overlap=10))

    assert table in fake_embed[0], "the table block must be embedded as one unit"


def test_semantic_keeps_chunk_index_continuous_across_a_split_group(fake_embed):
    """An oversize group falls back to fixed-size windows mid-section; the index
    has to keep counting, not restart, or two chunks collide on doc_id."""
    long_a = "Alpha " + ALPHABET[:120] + "."
    chunks = chunk_semantic([_section(f"{long_a} Beta tail.")], _cfg(max_chars=40, overlap=10))

    indexes = [c.metadata["chunk_index"] for c in chunks]
    assert indexes == list(range(1, len(chunks) + 1))
    assert len(set(c.doc_id for c in chunks)) == len(chunks), "doc_ids must stay unique"
