"""Fixed-size sliding-window chunker (ported from the original char chunker)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from kb.chunkers.base import Chunk
from kb.chunkers import register_chunker
from kb.parsers.base import Section

if TYPE_CHECKING:
    from config.schema import ChunkingConfig


def window_text(text: str, max_chars: int, overlap: int) -> Iterable[str]:
    """Yield sliding windows over whitespace-normalized text."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        yield cleaned
        return
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + max_chars)
        chunk = cleaned[start:end]
        if chunk:
            yield chunk
        if end == len(cleaned):
            break
        start = max(0, end - overlap)


def split_section(section: Section, cfg: "ChunkingConfig") -> list[Chunk]:
    base_id = section.doc_id or "chunk"
    chunks: list[Chunk] = []
    for idx, piece in enumerate(window_text(section.text, cfg.max_chars, cfg.overlap), start=1):
        chunks.append(
            Chunk(
                text=piece,
                metadata={**section.metadata, "chunk_index": idx},
                doc_id=f"{base_id}:c{idx}",
            )
        )
    return chunks


@register_chunker("fixed_size")
def chunk_fixed_size(sections: list[Section], cfg: "ChunkingConfig") -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in sections:
        chunks.extend(split_section(section, cfg))
    return chunks
