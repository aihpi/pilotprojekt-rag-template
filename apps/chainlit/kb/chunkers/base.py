"""Chunker interface: sections in -> chunks out, metadata preserved.

A chunker decides whether/how to split parser-produced sections. The heading
splitting itself happens in the parser; chunkers only re-split section text
(fixed-size windows) or pass sections through unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from kb.parsers.base import Section

if TYPE_CHECKING:
    from config.schema import ChunkingConfig


@dataclass
class Chunk:
    text: str
    metadata: dict[str, Any]
    doc_id: str


ChunkerFn = Callable[[list[Section], "ChunkingConfig"], list[Chunk]]
