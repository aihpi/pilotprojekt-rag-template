"""Semantic chunker: split each section at embedding-similarity breakpoints.

Sentences within a parsed section are embedded, and a new chunk starts wherever
the cosine distance between consecutive sentences exceeds a percentile threshold
(the "percentile" method). Groups larger than ``2 * max_chars`` fall back to
fixed-size windows so a run without breakpoints can't blow past embedding limits.

Unlike the other chunkers this one is NOT free: it embeds every sentence at
ingest time (via the synchronous ``llm.embed_sync``, because chunking runs inside
``ingest_all``'s event loop). Best for long, unstructured text; heading-delimited
docs are usually served just as well by the cheaper ``heading`` strategy.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from kb.chunkers.base import Chunk
from kb.chunkers import register_chunker
from kb.chunkers.fixed_size import window_text
from kb.parsers.base import Section

if TYPE_CHECKING:
    from config.schema import ChunkingConfig

# Sentence boundary: end punctuation followed by whitespace and a likely start.
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")


def _split_sentences(text: str) -> list[str]:
    """Split into sentences, keeping blank-line-delimited blocks (e.g. a
    serialized table, which has no sentence punctuation) intact."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        for sentence in _SENTENCE.split(para):
            sentence = sentence.strip()
            if sentence:
                out.append(sentence)
    return out


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _emit(text: str, section: Section, base_id: str, start_index: int, cfg: "ChunkingConfig") -> list[Chunk]:
    text = text.strip()
    if not text:
        return []
    pieces = (
        [text]
        if len(text) <= 2 * cfg.max_chars
        else list(window_text(text, cfg.max_chars, cfg.overlap))
    )
    chunks: list[Chunk] = []
    for offset, piece in enumerate(pieces):
        idx = start_index + offset
        chunks.append(
            Chunk(
                text=piece,
                metadata={**section.metadata, "chunk_index": idx},
                doc_id=f"{base_id}:c{idx}",
            )
        )
    return chunks


@register_chunker("semantic")
def chunk_semantic(sections: list[Section], cfg: "ChunkingConfig") -> list[Chunk]:
    from llm import embed_sync

    chunks: list[Chunk] = []
    for section in sections:
        base_id = section.doc_id or "chunk"
        sentences = _split_sentences(section.text)

        if len(sentences) <= 1:
            groups = [section.text]
        else:
            vectors = embed_sync(sentences)
            distances = [
                _cosine_distance(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)
            ]
            threshold = _percentile(distances, cfg.semantic_breakpoint_percentile)
            groups = []
            current = [sentences[0]]
            for i, distance in enumerate(distances):
                if distance > threshold and current:
                    groups.append(" ".join(current))
                    current = []
                current.append(sentences[i + 1])
            if current:
                groups.append(" ".join(current))

        index = 0
        for group in groups:
            emitted = _emit(group, section, base_id, index + 1, cfg)
            index += len(emitted)
            chunks.extend(emitted)
    return chunks
