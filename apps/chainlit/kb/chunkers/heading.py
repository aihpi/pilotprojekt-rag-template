"""Heading chunker: one chunk per section, with an oversize safety guard.

Parser output is already heading-delimited, so this is passthrough — except a
section larger than ``2 * max_chars`` is split into fixed-size windows so a
single huge section can't blow past embedding limits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kb.chunkers.base import Chunk
from kb.chunkers import register_chunker
from kb.chunkers.fixed_size import split_section
from kb.parsers.base import Section

if TYPE_CHECKING:
    from config.schema import ChunkingConfig


@register_chunker("heading")
def chunk_heading(sections: list[Section], cfg: "ChunkingConfig") -> list[Chunk]:
    chunks: list[Chunk] = []
    for idx, section in enumerate(sections, start=1):
        if len(section.text) > 2 * cfg.max_chars:
            chunks.extend(split_section(section, cfg))
        else:
            chunks.append(
                Chunk(
                    text=section.text,
                    metadata=dict(section.metadata),
                    doc_id=section.doc_id or f"section:{idx}",
                )
            )
    return chunks
