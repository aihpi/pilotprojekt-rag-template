"""Passthrough chunker: exactly one chunk per section, never split.

Use for structured JSON/CSV records and for parser output that is already the
desired retrieval granularity (e.g. heading-delimited PDF sections).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kb.chunkers.base import Chunk
from kb.chunkers import register_chunker
from kb.parsers.base import Section

if TYPE_CHECKING:
    from config.schema import ChunkingConfig


@register_chunker("passthrough")
def chunk_passthrough(sections: list[Section], cfg: "ChunkingConfig") -> list[Chunk]:
    chunks: list[Chunk] = []
    for idx, section in enumerate(sections, start=1):
        chunks.append(
            Chunk(
                text=section.text,
                metadata=dict(section.metadata),
                doc_id=section.doc_id or f"section:{idx}",
            )
        )
    return chunks
