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
import re
import sqlite3
from typing import TYPE_CHECKING, Any

from tools import register_tool
from tools.base import ToolContext, ToolResult

if TYPE_CHECKING:
    from config.schema import RagConfig

_DISCOVERY_HEADERS = [
    "title", "first_author", "year", "doi",
    "surface", "diameter", "dye", "source", "mention_count", "review_decision",
]
_VERIFICATION_HEADERS = [
    "title", "first_author", "year", "doi",
    "surface", "diameter", "dye", "product_id", "confirmed", "page", "quote", "source", "review_decision",
]

_DESC = {
    "de": (
        "Durchsuche die PolyAn-Beads-Literaturdatenbank. "
        "Breite Anfragen (eine Eigenschaft, z.B. nur Oberfläche) geben eine Papierliste zurück – "
        "Zitate sind im Tool-Schritt sichtbar, nicht in der Antwort. "
        "Enge Anfragen (Produkt-ID oder mind. 2 Eigenschaften) geben volle Details inkl. Zitate zurück. "
        "Schreibe eine kurze Zusammenfassung; reproduziere die Tabelle nicht."
    ),
    "en": (
        "Search the PolyAn bead literature database. "
        "Broad queries (0-1 attribute, no product_id) return a compact paper list -- "
        "quotes are shown in the tool step panel, not in the payload. "
        "Narrow queries (product_id given OR 2+ of surface/diameter/dye specified) return full detail "
        "including quotes and the confirmed flag (true = product number was explicitly written in the paper). "
        "The full results table is already displayed in the tool step panel for the user to verify. "
        "Write a concise natural-language summary: how many papers matched, how many had a confirmed "
        "citation, and any notable patterns. Do not reproduce the table. "
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


def _query_discovery(conn: sqlite3.Connection, params: dict, limit: int | None) -> tuple[list[dict], str]:
    cap = min(limit, 100) if limit else 100
    sql = f"""SELECT
    p.title, p.year, p.doi, p.first_author,
    m.surface_catalog, m.diameter_catalog, m.dye_catalog,
    MIN(m.source) AS source,
    COUNT(*) AS mention_count,
    MIN(m.review_decision) AS review_decision
FROM paper p
JOIN mention m ON m.paper_id = p.id
WHERE (m.surface_catalog = :surface OR :surface IS NULL)
  AND (m.diameter_catalog = :diameter OR :diameter IS NULL)
  AND (m.dye_catalog = :dye OR :dye IS NULL)
  AND (m.review_decision != 'rejected' OR m.review_decision IS NULL)
GROUP BY p.doi, m.surface_catalog, m.diameter_catalog, m.dye_catalog
ORDER BY p.year DESC
LIMIT {cap}"""
    rows = [
        {
            "title": r["title"], "year": r["year"], "doi": r["doi"],
            "first_author": r["first_author"],
            "surface": r["surface_catalog"], "diameter": r["diameter_catalog"],
            "dye": r["dye_catalog"], "source": r["source"],
            "mention_count": r["mention_count"], "review_decision": r["review_decision"],
        }
        for r in conn.execute(sql, params).fetchall()
    ]
    return rows, sql


def _query_verification(conn: sqlite3.Connection, params: dict,
                        product_id: str | None, limit: int | None) -> tuple[list[dict], str]:
    limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
    if product_id is not None:
        sql = f"""SELECT
    p.title, p.year, p.doi, p.first_author,
    m.surface_catalog, m.diameter_catalog, m.dye_catalog,
    mp.product_id,
    MAX(CASE WHEN m.product_number_catalog = :product_id THEN 1 ELSE 0 END) AS confirmed,
    MIN(e.page) AS page,
    MIN(e.quote) AS quote,
    m.source,
    m.review_decision
FROM paper p
JOIN mention m ON m.paper_id = p.id
JOIN evidence e ON e.mention_id = m.id
JOIN mention_product mp ON mp.mention_id = m.id
WHERE mp.product_id = :product_id
  AND (m.surface_catalog = :surface OR :surface IS NULL)
  AND (m.diameter_catalog = :diameter OR :diameter IS NULL)
  AND (m.dye_catalog = :dye OR :dye IS NULL)
  AND (m.review_decision != 'rejected' OR m.review_decision IS NULL)
GROUP BY p.doi, mp.product_id, m.surface_catalog, m.diameter_catalog, m.dye_catalog, m.source, m.review_decision
ORDER BY p.year DESC
{limit_clause}"""
    else:
        sql = f"""SELECT
    p.title, p.year, p.doi, p.first_author,
    m.surface_catalog, m.diameter_catalog, m.dye_catalog,
    mp.product_id,
    MAX(CASE WHEN m.product_number_catalog IS NOT NULL THEN 1 ELSE 0 END) AS confirmed,
    MIN(e.page) AS page,
    MIN(e.quote) AS quote,
    m.source,
    m.review_decision
FROM paper p
JOIN mention m ON m.paper_id = p.id
JOIN evidence e ON e.mention_id = m.id
LEFT JOIN mention_product mp ON mp.mention_id = m.id
WHERE (m.surface_catalog = :surface OR :surface IS NULL)
  AND (m.diameter_catalog = :diameter OR :diameter IS NULL)
  AND (m.dye_catalog = :dye OR :dye IS NULL)
  AND (m.review_decision != 'rejected' OR m.review_decision IS NULL)
GROUP BY p.doi, m.surface_catalog, m.diameter_catalog, m.dye_catalog, mp.product_id, m.source, m.review_decision
ORDER BY p.year DESC
{limit_clause}"""
    rows = [
        {
            "title": r["title"], "year": r["year"], "doi": r["doi"],
            "first_author": r["first_author"],
            "surface": r["surface_catalog"], "diameter": r["diameter_catalog"],
            "dye": r["dye_catalog"], "product_id": r["product_id"],
            "confirmed": bool(r["confirmed"]),
            "page": r["page"], "quote": r["quote"], "source": r["source"],
            "review_decision": r["review_decision"],
        }
        for r in conn.execute(sql, params).fetchall()
    ]
    return rows, sql


def _cell(v: Any) -> str:
    if v is None:
        return ""
    return str(v).replace("|", "\\|").replace("\n", " ").replace("\r", "")


def _as_markdown_table(rows: list[dict], headers: list[str]) -> str:
    header_row = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    lines = [header_row, sep]
    for r in rows:
        lines.append("| " + " | ".join(_cell(r.get(h)) for h in headers) + " |")
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

    params = {"surface": surface, "diameter": diameter, "dye": dye, "product_id": product_id}
    filters = {k: v for k, v in params.items() if v}

    attr_count = sum(1 for x in [surface, diameter, dye] if x)
    is_narrow = product_id is not None or attr_count >= 2
    mode = "verification" if is_narrow else "discovery"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if is_narrow:
            rows, sql = _query_verification(conn, params, product_id, limit)
            headers = _VERIFICATION_HEADERS
        else:
            rows, sql = _query_discovery(conn, params, limit)
            headers = _DISCOVERY_HEADERS
    finally:
        conn.close()

    if is_narrow:
        reason = "product_id given" if product_id else f"{attr_count} attributes specified"
        mode_line = f"**Mode: verification** ({reason} -- full detail, no cap)"
    else:
        mode_line = f"**Mode: discovery** ({attr_count} attribute(s) -- compact overview, capped at 100)"

    # Substitute bound parameters into the SQL for display (single-pass, values only, not executable)
    sql_display = re.sub(r":(\w+)", lambda m: repr(params[m.group(1)]) if m.group(1) in params else m.group(0), sql)

    step = (
        mode_line + "\n\n"
        + "```sql\n" + sql_display + "\n```\n\n"
        + _as_markdown_table(rows, headers)
    )

    return ToolResult(
        payload={"mode": mode, "filters_applied": filters, "count": len(rows), "results": rows},
        results=[],
        step_output=step,
    )
