"""``verify_claim`` — re-retrieve to check whether a drafted statement is supported.

A hallucination guard: the agent passes a claim it is about to make; this
re-queries the knowledge base and returns the supporting evidence plus a
``supported`` signal. Returns RagResult items so the evidence is citable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools import register_tool
from tools.base import ToolContext, ToolResult

if TYPE_CHECKING:
    from config.schema import RagConfig

_DESC = {
    "de": "Prüft, ob eine geplante Aussage durch die Wissensbasis gestützt wird: "
    "sucht erneut nach Belegstellen und liefert die Evidenz plus ein "
    "supported-Signal. Vor unsicheren Behauptungen verwenden.",
    "en": "Check whether a statement you are about to make is supported by the "
    "knowledge base: re-retrieves evidence and returns it plus a supported flag. "
    "Use before making an uncertain claim.",
}


def _schema(cfg: "RagConfig") -> dict[str, Any]:
    lang = "de" if (cfg.language or "en").lower().startswith("de") else "en"
    description = cfg.tools.descriptions.get("verify_claim") or _DESC[lang]
    claim_desc = (
        "Die zu überprüfende Aussage." if lang == "de" else "The claim to verify."
    )
    return {
        "type": "function",
        "function": {
            "name": "verify_claim",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"claim": {"type": "string", "description": claim_desc}},
                "required": ["claim"],
            },
        },
    }


@register_tool("verify_claim", build_schema=_schema)
async def _verify_claim(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from rag_tool import render_context, verify_claim

    claim = str(args.get("claim") or "")
    if not claim:
        return ToolResult(payload={"error": "claim is required"}, results=[])

    results, supported = await verify_claim(
        claim, filters=dict(ctx.filters), collection=ctx.collection
    )
    context, cites, kept = render_context(results)
    payload = {
        "claim": claim,
        "supported": supported,
        "context": context,
        "citations": cites,
    }
    step = {"supported": supported, "hits": len(kept)}
    if len(results) != len(kept):
        step["omitted"] = len(results) - len(kept)
    return ToolResult(payload=payload, results=kept, step_output=step)
