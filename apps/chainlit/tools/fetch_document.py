"""``fetch_document`` — load a whole document into context, in reading order.

The right tool for summaries / overviews, where semantic top-k pulls scattered
fragments. Returns all chunks of one ``source_file`` as RagResult items, so the
citation panel still works. Capped at ``config.tools.fetch_max_chunks``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools import register_tool
from tools.base import ToolContext, ToolResult

if TYPE_CHECKING:
    from config.schema import RagConfig

_DESC = {
    "de": "Lädt ein komplettes Dokument (alle Abschnitte in Lesereihenfolge) anhand "
    "seines exakten source_file. Für Zusammenfassungen/Überblicke besser als die "
    "Suche. Hole den exakten source_file zuerst über list_documents.",
    "en": "Load an entire document (all sections in reading order) by its exact "
    "source_file. Better than search for summaries/overviews. Get the exact "
    "source_file from list_documents first.",
}


def _schema(cfg: "RagConfig") -> dict[str, Any]:
    lang = "de" if (cfg.language or "en").lower().startswith("de") else "en"
    description = cfg.tools.descriptions.get("fetch_document") or _DESC[lang]
    sf_desc = (
        "Exakter source_file des Dokuments (siehe list_documents)."
        if lang == "de"
        else "Exact source_file of the document (from list_documents)."
    )
    return {
        "type": "function",
        "function": {
            "name": "fetch_document",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"source_file": {"type": "string", "description": sf_desc}},
                "required": ["source_file"],
            },
        },
    }


@register_tool("fetch_document", build_schema=_schema)
async def _fetch_document(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from rag_tool import fetch_document, render_context

    source_file = str(args.get("source_file") or "")
    if not source_file:
        return ToolResult(payload={"error": "source_file is required"}, results=[])

    cap = ctx.fetch_max_chunks
    results = await fetch_document(source_file, collection=ctx.collection, max_chunks=cap)
    if not results:
        return ToolResult(
            payload={
                "source_file": source_file,
                "error": "no document matched that source_file — call list_documents for exact ids",
            },
            results=[],
        )
    context, cites, kept = render_context(results)
    # Either bound truncates the document the model is shown: the scroll cap, or the
    # context budget dropping chunks from the tail. Reporting only the first told the
    # model `truncated: false` for a context that carried a "Kontext gekürzt" notice.
    truncated = len(results) >= cap or len(kept) < len(results)
    payload = {
        "source_file": source_file,
        "chunks": len(kept),
        "truncated": truncated,
        "context": context,
        "citations": cites,
    }
    step: dict[str, Any] = {"chunks": len(kept)}
    if len(results) != len(kept):
        step["omitted"] = len(results) - len(kept)
    return ToolResult(payload=payload, results=kept, step_output=step)
