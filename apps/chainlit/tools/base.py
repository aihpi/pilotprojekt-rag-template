"""Core types for the pluggable agentic-RAG tools.

A tool bundles a config-aware OpenAI function *schema* with an async *handler*.
The handler receives the model's parsed arguments plus a :class:`ToolContext`
(state injected by ``app.py`` — handlers must never import ``app``) and returns a
:class:`ToolResult`: a JSON-serializable ``payload`` for the tool-role message,
plus ``results`` (RagResult-shaped items) that feed the existing citation panel.

Import-cycle rule: this module and every tool module import ``rag_tool`` only
inside handler bodies / under ``TYPE_CHECKING`` — never at module top — so that
``import tools`` stays free of the ``rag_tool -> settings -> get_config()`` chain
(``settings`` builds the config at import time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from config.schema import RagConfig
    from rag_tool import RagResult


@dataclass
class ToolContext:
    """Runtime state a handler may need, injected per tool call by ``app.py``."""

    query_fallback: str = ""            # message.content, for an empty-query defense
    filters: dict[str, Any] = field(default_factory=dict)  # _active_retrieval_filters()
    default_top_k: int = 5              # TOP_K
    max_top_k: int = 5                  # MAX_TOP_K
    collection: str | None = None       # None -> backends use QDRANT_COLLECTION
    language: str = "en"
    fetch_max_chunks: int = 200         # cfg.tools.fetch_max_chunks
    expand_window: int = 1              # cfg.tools.expand_window


@dataclass
class ToolResult:
    payload: dict[str, Any]                          # JSON-serializable -> tool message
    results: list["RagResult"] = field(default_factory=list)  # -> aggregation + citations
    step_output: dict[str, Any] | None = None        # optional cl.Step.output override


SchemaBuilder = Callable[["RagConfig"], dict[str, Any]]
ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass
class Tool:
    id: str                      # stable registry key ("search", "fetch_document", ...)
    build_schema: SchemaBuilder  # config-aware OpenAI function-tool schema
    handler: ToolHandler         # async dispatch handler


def clamp_top_k(raw: Any, default: int, maximum: int) -> int:
    """Mirror app.py's top_k coercion: int-or-default, floored at 1, capped."""
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))
