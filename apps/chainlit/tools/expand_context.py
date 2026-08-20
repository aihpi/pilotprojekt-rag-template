"""``expand_context`` — widen a promising hit by pulling its neighboring chunks.

Given a document's ``source_file`` and a chunk's ``section_index``, returns the
chunks within ±``window`` sections, in order — counters RAG's "the chunk was too
small / cut off" failure. Returns RagResult items so citations still work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools import register_tool
from tools.base import ToolContext, ToolResult

if TYPE_CHECKING:
    from config.schema import RagConfig

_DESC = {
    "de": "Erweitert einen Treffer um seine Nachbarabschnitte: gibt die Abschnitte "
    "innerhalb von ±window um section_index im angegebenen Dokument zurück. Nützlich, "
    "wenn ein Suchtreffer abgeschnitten wirkt.",
    "en": "Widen a hit with its neighbors: returns the sections within ±window of "
    "section_index in the given document. Use when a search hit seems cut off.",
}


def _schema(cfg: "RagConfig") -> dict[str, Any]:
    lang = "de" if (cfg.language or "en").lower().startswith("de") else "en"
    description = cfg.tools.descriptions.get("expand_context") or _DESC[lang]
    return {
        "type": "function",
        "function": {
            "name": "expand_context",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "source_file": {"type": "string", "description": "Exact source_file."},
                    "section_index": {
                        "type": "integer",
                        "description": "The section_index of the chunk to expand around.",
                    },
                    "window": {
                        "type": "integer",
                        "description": "How many neighboring sections on each side.",
                        "default": cfg.tools.expand_window,
                    },
                },
                "required": ["source_file", "section_index"],
            },
        },
    }


@register_tool("expand_context", build_schema=_schema)
async def _expand_context(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from rag_tool import expand_context, render_context

    source_file = str(args.get("source_file") or "")
    raw_index = args.get("section_index")
    if not source_file or raw_index is None:
        return ToolResult(
            payload={"error": "source_file and section_index are required"}, results=[]
        )
    try:
        section_index = int(raw_index)
    except (TypeError, ValueError):
        return ToolResult(payload={"error": "section_index must be an integer"}, results=[])

    try:
        window = int(args.get("window", ctx.expand_window))
    except (TypeError, ValueError):
        window = ctx.expand_window
    window = max(0, window)

    results = await expand_context(
        source_file, section_index, window=window, collection=ctx.collection
    )
    context, cites, kept = render_context(results)
    payload = {
        "source_file": source_file,
        "section_index": section_index,
        "window": window,
        "chunks": len(kept),
        "context": context,
        "citations": cites,
    }
    step: dict[str, Any] = {"chunks": len(kept)}
    if len(results) != len(kept):
        step["omitted"] = len(results) - len(kept)
    return ToolResult(payload=payload, results=kept, step_output=step)
