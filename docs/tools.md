# Agentic tools

Classic RAG retrieves once and answers. This template instead exposes a **set of
tools** to the model and lets it decide what to call, how often, and in which
order — list the knowledge base, pull a whole document, widen a hit, verify a
claim. Which tools exist is a config decision; every tool lives in
`apps/chainlit/tools/` and registers itself in a registry.

```yaml
tools:
  enabled: [search, list_documents, fetch_document, expand_context, verify_claim]
  descriptions:
    list_documents: "your own wording — this is what the model reads"
  fetch_max_chunks: 200   # whole-document size cap for fetch_document
  expand_window: 1        # default neighbor window for expand_context
```

!!! note "The default is single-tool RAG"
    `tools.enabled` defaults to `[search]`, so an instance that only declares the
    classic `tool:` block keeps behaving exactly as before. Order matters: it is
    the order in which the schemas are handed to the model. The env override
    `RAG_TOOLS_ENABLED` takes a `||`-separated list.

## The five built-in tools

| Tool | What it does | Citations |
|---|---|---|
| `search` | Semantic top-k retrieval | yes |
| `list_documents` | Enumerate the knowledge base | no (navigational) |
| `fetch_document` | Load one whole document in reading order | yes |
| `expand_context` | Neighboring sections around a hit | yes |
| `verify_claim` | Re-retrieve evidence for a drafted statement | yes |

### `search`

Semantic top-k retrieval — the original single tool. Parameters: `query`,
`top_k`, and an optional `document` (an **exact** `source_file`) to scope the
search to one document. Scoping requires `retrieval.filterable_fields:
[source_file]`, otherwise the filter is ignored. For backwards compatibility its
function name and descriptions come from the `tool:` block, not from
`tools.descriptions`.

### `list_documents`

Lists every document in the collection: exact `source_file`, title, chunk count
and an approximate token size. It takes no parameters and is purely
**navigational** — it returns no citations. It is what lets the model turn a
fuzzy user reference ("the Kage 2018 paper") into the exact `source_file` that
`fetch_document` and `expand_context` require, and it answers "which documents
are in the knowledge base?" directly.

### `fetch_document`

Loads a **complete** document, all sections in reading order, by its exact
`source_file`. This is the right tool for summaries and overviews, where
semantic top-k only ever returns scattered fragments. The result is capped at
`tools.fetch_max_chunks` and the payload sets `truncated: true` when the cap
was hit, so the model knows it did not see the whole text.

### `expand_context`

Returns the sections within ±`window` of a given `section_index` in a document
(`source_file`, `section_index`, `window`). Use it against RAG's classic "the
chunk was too small" failure: the model saw a promising hit that looks cut off
and pulls its neighbors instead of guessing.

### `verify_claim`

A hallucination guard. The model passes a statement it is about to make; the
tool re-queries the knowledge base and returns the supporting passages plus a
`supported` signal, so an unsupported claim can be dropped or hedged before it
reaches the user.

## Descriptions are the prompt

A tool's description is the only thing the model knows about it, so it is the
main tuning knob. Every built-in tool ships language-aware defaults in German
and English, selected by the top-level `language:` field of your config (see
[Configuration](configuration.md)). Override any of them per tool id under
`tools.descriptions` — useful to speak your domain's vocabulary ("paper",
"Baustein", "ticket") instead of the generic "document".

## Writing your own tool

A tool is a schema builder (returns an OpenAI function schema as a `dict`) plus
an async handler `(args: dict, ctx: ToolContext) -> ToolResult`. The types live
in [`tools/base.py`](https://github.com/aihpi/pilotprojekt-rag-template/blob/main/apps/chainlit/tools/base.py).

```python
# apps/chainlit/tools/count_pages.py
from typing import Any

from tools import register_tool
from tools.base import ToolContext, ToolResult


def _schema(cfg) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "count_pages",
            "description": "Page count of one document, by its exact source_file.",
            "parameters": {
                "type": "object",
                "properties": {"source_file": {"type": "string"}},
                "required": ["source_file"],
            },
        },
    }


@register_tool("count_pages", build_schema=_schema)
async def _count_pages(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from rag_tool import fetch_document          # inside the handler — see below

    results = await fetch_document(
        str(args.get("source_file") or ""),
        collection=ctx.collection,
        max_chunks=ctx.fetch_max_chunks,
    )
    pages = {(r.metadata or {}).get("page_start") for r in results} - {None}
    return ToolResult(payload={"pages": len(pages)}, results=[])
```

Import the module at the bottom of `tools/__init__.py` so the decorator runs,
then add the id to `tools.enabled`.

!!! warning "Two rules that will bite you"
    **Citations come from `results`.** `ToolResult.results` must be
    RagResult-shaped (`.text`, `.score`, `.metadata`) — those items feed the
    aggregation and the citation panel. A purely navigational tool returns
    `results=[]`; the `payload` alone is what the model reads.

    **Import `rag_tool` inside the handler body**, never at module top. `tools`
    must stay free of the `rag_tool → settings → get_config()` chain, because
    `settings` builds the config at import time — a top-level import creates a
    cycle.

## Invalid ids fail fast

An unknown id in `tools.enabled` is rejected while the config loads, and the
validator lists the registered ids in the error. A typo therefore surfaces at
startup instead of as a silently missing capability at query time.
