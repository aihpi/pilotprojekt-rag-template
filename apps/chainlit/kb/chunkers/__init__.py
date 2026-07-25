"""Chunker registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kb.chunkers.base import Chunk, ChunkerFn

if TYPE_CHECKING:
    from config.schema import ChunkingConfig

CHUNKER_REGISTRY: dict[str, ChunkerFn] = {}


def register_chunker(*names: str):
    def deco(fn: ChunkerFn) -> ChunkerFn:
        for name in names:
            CHUNKER_REGISTRY[name] = fn
        return fn

    return deco


def get_chunker(strategy: str) -> ChunkerFn:
    chunker = CHUNKER_REGISTRY.get(strategy)
    if chunker is None:
        raise KeyError(
            f"no chunker registered for strategy '{strategy}'. "
            f"Registered: {sorted(CHUNKER_REGISTRY)}"
        )
    return chunker


from kb.chunkers import (  # noqa: E402,F401
    docling_hybrid,
    fixed_size,
    heading,
    passthrough,
    semantic,
)

__all__ = ["CHUNKER_REGISTRY", "register_chunker", "get_chunker", "Chunk"]
