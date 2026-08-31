"""``search_bead_literature`` -- query the PolyAn bead literature database.

Searches panda.db (a SQLite database built by the panda-db pipeline) for
papers that mention PolyAn beads matching the given attributes. Returns
matching papers, the bead attributes found, candidate catalog product IDs,
and the exact quote from the paper.

Configuration:
    PANDA_DB_PATH  path to panda.db (required; tool returns an error if unset)
"""

from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING, Any

from tools import register_tool
from tools.base import ToolContext, ToolResult

if TYPE_CHECKING:
    from config.schema import RagConfig

_DESC = {
    "de": (
        "Durchsuche die PolyAn-Beads-Literaturdatenbank nach Papieren, die Beads "
        "mit bestimmten Eigenschaften verwendet haben (Oberfläche, Durchmesser, Dye, "
        "Produktnummer). Die vollständige Ergebnistabelle wird bereits im Tool-Schritt "
        "angezeigt. Schreibe eine kurze Zusammenfassung: wie viele Treffer, wie viele "
        "mit bestätigter Zitation (confirmed=true bedeutet, die Produktnummer stand "
        "explizit im Paper), und auffällige Muster. Die Tabelle nicht wiederholen."
    ),
    "en": (
        "Search the PolyAn bead literature database. Returns papers that used beads "
        "matching the given attributes (surface coating, diameter, dye, or product number). "
        "The full results table is already displayed in the tool step panel for the user "
        "to verify. Write a concise natural-language summary: how many papers matched, "
        "how many had a confirmed citation (confirmed=true means the product number was "
        "explicitly written in the paper), and any notable patterns. "
        "Do not reproduce the table in your response. "
        "Use when the user asks which papers used a specific bead type or product."
    ),
}


def _schema(cfg: "RagConfig") -> dict[str, Any]:
    lang = "de" if (cfg.language or "en").lower().startswith("de") else "en"
    description = cfg.tools.descriptions.get("search_bead_literature") or _DESC[lang]
    return {
        "type": "function",
        "function": {
            "name": "search_bead_literature",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "surface": {
                        "type": "string",
                        "description": (
                            "Surface coating, e.g. 'Streptavidin', 'Carboxy', "
                            "'NeutrAvidin', 'Aldehyde'. Must match catalog vocabulary exactly."
                        ),
                    },
                    "diameter": {
                        "type": "string",
                        "description": (
                            "Bead diameter, e.g. '1 µm', '6.5 µm', '500 nm'. "
                            "Must match catalog vocabulary exactly."
                        ),
                    },
                    "dye": {
                        "type": "string",
                        "description": (
                            "Fluorescent dye, e.g. 'PolyAn Red4', 'PolyAn Green', "
                            "'PolyAn Blue'. Must match catalog vocabulary exactly."
                        ),
                    },
                    "product_id": {
                        "type": "string",
                        "description": "Exact 8-digit PolyAn catalog product number, e.g. '11000006'.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Maximum results to return. Only set this if the user explicitly "
                            "asks for a specific number, e.g. 'find me 5 papers'. "
                            "Omit otherwise -- all matching papers are returned."
                        ),
                    },
                },
                "required": [],
            },
        },
    }


def _query(db_path: str, surface: str | None, diameter: str | None,
           dye: str | None, product_id: str | None, limit: int | None) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
    try:
        params = {
            "surface": surface,
            "diameter": diameter,
            "dye": dye,
            "product_id": product_id,
        }

        if product_id is not None:
            # confirmed=1: product number was explicitly in the paper text.
            # confirmed=0: pipeline-resolved candidate.
            sql = f"""
                SELECT DISTINCT
                    p.title, p.year, p.doi, p.first_author,
                    m.surface_catalog, m.diameter_catalog, m.dye_catalog,
                    mp.product_id,
                    CASE WHEN m.product_number_catalog = :product_id THEN 1 ELSE 0 END AS confirmed,
                    e.quote, e.page, m.source
                FROM paper p
                JOIN mention m ON m.paper_id = p.id
                JOIN evidence e ON e.mention_id = m.id
                JOIN mention_product mp ON mp.mention_id = m.id
                WHERE mp.product_id = :product_id
                  AND (m.surface_catalog = :surface OR :surface IS NULL)
                  AND (m.diameter_catalog = :diameter OR :diameter IS NULL)
                  AND (m.dye_catalog = :dye OR :dye IS NULL)
                {limit_clause}
            """
        else:
            sql = f"""
                SELECT DISTINCT
                    p.title, p.year, p.doi, p.first_author,
                    m.surface_catalog, m.diameter_catalog, m.dye_catalog,
                    mp.product_id,
                    0 AS confirmed,
                    e.quote, e.page, m.source
                FROM paper p
                JOIN mention m ON m.paper_id = p.id
                JOIN evidence e ON e.mention_id = m.id
                LEFT JOIN mention_product mp ON mp.mention_id = m.id
                WHERE (m.surface_catalog = :surface OR :surface IS NULL)
                  AND (m.diameter_catalog = :diameter OR :diameter IS NULL)
                  AND (m.dye_catalog = :dye OR :dye IS NULL)
                {limit_clause}
            """
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    seen: set[tuple] = set()
    results = []
    for row in rows:
        key = (row["doi"], row["product_id"])
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "title": row["title"],
            "year": row["year"],
            "doi": row["doi"],
            "first_author": row["first_author"],
            "surface": row["surface_catalog"],
            "diameter": row["diameter_catalog"],
            "dye": row["dye_catalog"],
            "product_id": row["product_id"],
            "confirmed": bool(row["confirmed"]),
            "page": row["page"],
            "quote": row["quote"],
            "source": row["source"],
        })
    return results


def _as_markdown_table(rows: list[dict]) -> str:
    headers = ["title", "first_author", "year", "doi", "surface", "diameter",
               "dye", "product_id", "confirmed", "page", "quote", "source"]
    header_row = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    lines = [header_row, sep]
    for r in rows:
        cells = [str(r.get(h) or "") for h in headers]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


@register_tool("search_bead_literature", build_schema=_schema)
async def _search_bead_literature(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    db_path = os.environ.get("PANDA_DB_PATH", "")
    if not db_path or not os.path.exists(db_path):
        msg = (
            "PANDA_DB_PATH is not configured or the file does not exist. "
            "Set PANDA_DB_PATH to the absolute path of panda.db."
        )
        return ToolResult(payload={"error": msg}, results=[])

    surface = args.get("surface") or None
    diameter = args.get("diameter") or None
    dye = args.get("dye") or None
    product_id = args.get("product_id") or None
    raw_limit = args.get("limit")
    limit = max(1, int(raw_limit)) if raw_limit is not None else None

    filters = {k: v for k, v in {
        "product_id": product_id, "surface": surface,
        "diameter": diameter, "dye": dye,
    }.items() if v}

    rows = _query(db_path, surface, diameter, dye, product_id, limit)
    return ToolResult(
        payload={"filters_applied": filters, "count": len(rows), "results": rows},
        results=[],
        step_output=_as_markdown_table(rows),
    )
