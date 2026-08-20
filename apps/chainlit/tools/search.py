"""``search`` — semantic top-k retrieval (the original single tool).

Back-compat: its OpenAI function name and descriptions come from the existing
``config.tool`` block, and its payload shape stays ``{query, context, citations}``
so nothing downstream changes for search-only instances.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools import register_tool
from tools.base import ToolContext, ToolResult, clamp_top_k

if TYPE_CHECKING:
    from config.schema import RagConfig


def _schema(cfg: "RagConfig") -> dict[str, Any]:
    tool = cfg.tool
    de = (cfg.language or "en").lower().startswith("de")
    document_desc = (
        "Optionaler exakter source_file, um die Suche auf ein Dokument einzuschränken."
        if de
        else "Optional exact source_file to scope the search to one document."
    )
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": tool.query_param_description},
                    "top_k": {
                        "type": "integer",
                        "description": tool.top_k_param_description,
                        "default": cfg.retrieval.top_k,
                    },
                    "document": {"type": "string", "description": document_desc},
                },
                "required": ["query"],
            },
        },
    }


@register_tool("search", build_schema=_schema)
async def _search(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from rag_tool import render_context, retrieve

    query = str(args.get("query") or ctx.query_fallback or "")
    top_k = clamp_top_k(args.get("top_k"), ctx.default_top_k, ctx.max_top_k)
    filters = dict(ctx.filters)
    document = args.get("document")
    if document:
        filters.setdefault("source_file", document)  # applies iff source_file is filterable
    results = await retrieve(query, top_k, filters=filters, collection=ctx.collection)
    context, cites, kept = render_context(results)
    payload = {"query": query, "context": context, "citations": cites}
    # `kept`, not `results`: the citation panel and the aggregation must describe
    # the same chunks the model was actually given.
    return ToolResult(payload=payload, results=kept)
