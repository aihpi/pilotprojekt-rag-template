"""``list_documents`` — enumerate the knowledge base (navigational, no citations).

Lets the agent resolve fuzzy names ("Kage 2018") to the exact ``source_file``
needed by ``fetch_document`` / ``expand_context``, and answer "which documents
are in the KB?". Returns per-document chunk counts and a rough token size.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools import register_tool
from tools.base import ToolContext, ToolResult

if TYPE_CHECKING:
    from config.schema import RagConfig

_DESC = {
    "de": "Liste aller Dokumente in der Wissensbasis (exakter source_file, Titel, "
    "Anzahl der Abschnitte, ungefähre Tokenzahl). Nützlich, um Dokumentnamen "
    "aufzulösen oder zu fragen, welche Dokumente vorhanden sind.",
    "en": "List every document in the knowledge base (exact source_file, title, "
    "chunk count, approx tokens). Use it to resolve a document name or to answer "
    "which documents exist.",
}


def _schema(cfg: "RagConfig") -> dict[str, Any]:
    lang = "de" if (cfg.language or "en").lower().startswith("de") else "en"
    description = cfg.tools.descriptions.get("list_documents") or _DESC[lang]
    return {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


@register_tool("list_documents", build_schema=_schema)
async def _list_documents(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from rag_tool import list_documents

    documents = await list_documents(collection=ctx.collection)
    return ToolResult(
        payload={"documents": documents, "count": len(documents)},
        results=[],
        step_output={"documents": len(documents)},
    )
