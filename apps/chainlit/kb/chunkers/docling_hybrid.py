"""``docling_hybrid`` chunker.

The actual token-aware chunking happens in the PDF parser, which runs Docling's
``HybridChunker`` directly on the ``DoclingDocument`` (it needs the document, not
our reconstructed section text) and emits one section per hybrid chunk — tables
and figures already serialized, heading trail contextualized. So this chunker is
a pass-through: it turns those pre-built sections into chunks unchanged.

Only meaningful for PDF sources; with any other format the source's sections are
passed through as-is (no token-aware splitting happens).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kb.chunkers.base import Chunk
from kb.chunkers import register_chunker
from kb.parsers.base import Section

if TYPE_CHECKING:
    from config.schema import ChunkingConfig


@register_chunker("docling_hybrid")
def chunk_docling_hybrid(sections: list[Section], cfg: "ChunkingConfig") -> list[Chunk]:
    return [
        Chunk(
            text=section.text,
            metadata=dict(section.metadata),
            doc_id=section.doc_id or f"chunk:{idx}",
        )
        for idx, section in enumerate(sections, start=1)
    ]
