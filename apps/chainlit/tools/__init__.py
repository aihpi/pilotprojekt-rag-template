"""Tool registry for the agentic-RAG toolset.

Mirrors the ``kb/chunkers`` / ``kb/parsers`` convention: a module-level registry
dict, a ``register_tool`` decorator, a ``get_tool`` lookup that raises a helpful
``KeyError``, and lazy submodule imports at the bottom so each tool self-registers
at package-import time. Which tools are exposed to the model is driven by
``config.tools.enabled`` (see :func:`build_openai_tools`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools.base import (
    SchemaBuilder,
    Tool,
    ToolContext,
    ToolHandler,
    ToolResult,
    clamp_top_k,
)

if TYPE_CHECKING:
    from config.schema import RagConfig

TOOL_REGISTRY: dict[str, Tool] = {}


def register_tool(tool_id: str, *, build_schema: SchemaBuilder):
    def deco(handler: ToolHandler) -> ToolHandler:
        TOOL_REGISTRY[tool_id] = Tool(id=tool_id, build_schema=build_schema, handler=handler)
        return handler

    return deco


def get_tool(tool_id: str) -> Tool:
    tool = TOOL_REGISTRY.get(tool_id)
    if tool is None:
        raise KeyError(
            f"no tool registered for '{tool_id}'. Registered: {sorted(TOOL_REGISTRY)}"
        )
    return tool


def enabled_tool_ids(cfg: "RagConfig") -> list[str]:
    return list(cfg.tools.enabled) or ["search"]


def build_openai_tools(cfg: "RagConfig") -> tuple[list[dict[str, Any]], dict[str, Tool]]:
    """Return ``(schemas, by_function_name)`` for the enabled tools:
    the OpenAI schema list to pass to ``chat(tools=...)`` and a router map from
    each schema's function name to its :class:`Tool`."""
    schemas: list[dict[str, Any]] = []
    by_function_name: dict[str, Tool] = {}
    for tool_id in enabled_tool_ids(cfg):
        tool = get_tool(tool_id)
        schema = tool.build_schema(cfg)
        schemas.append(schema)
        by_function_name[schema["function"]["name"]] = tool
    return schemas, by_function_name


# Lazy imports populate the registry. Each module imports only tools/tools.base
# at top and defers `rag_tool` imports into its handler, so this stays cycle-free.
from tools import (  # noqa: E402,F401
    expand_context,
    fetch_document,
    list_documents,
    search,
    verify_claim,
)

__all__ = [
    "TOOL_REGISTRY",
    "register_tool",
    "get_tool",
    "enabled_tool_ids",
    "build_openai_tools",
    "clamp_top_k",
    "Tool",
    "ToolContext",
    "ToolResult",
]
