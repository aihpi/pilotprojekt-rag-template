# Agentische Tools

Klassisches RAG ruft einmal ab und antwortet. Dieses Template stellt dem Modell
stattdessen einen **Satz von Tools** bereit und lässt es entscheiden, was es
aufruft, wie oft und in welcher Reihenfolge — Wissensbasis auflisten, ein
vollständiges Dokument laden, einen Treffer erweitern, eine Aussage prüfen.
Welche Tools existieren, ist eine Konfigurationsentscheidung; jedes Tool liegt in
`apps/chainlit/tools/` und registriert sich selbst in einer Registry.

```yaml
tools:
  enabled: [search, list_documents, fetch_document, expand_context, verify_claim]
  descriptions:
    list_documents: "eigene Beschreibung — genau das liest das Modell"
  fetch_max_chunks: 200   # Obergrenze für die Dokumentgröße bei fetch_document
  expand_window: 1        # Standard-Nachbarfenster für expand_context
```

!!! note "Der Default ist Ein-Tool-RAG"
    `tools.enabled` ist standardmäßig `[search]`, sodass eine Instanz, die nur
    den klassischen `tool:`-Block deklariert, sich exakt wie bisher verhält. Die
    Reihenfolge zählt: sie ist die Reihenfolge, in der die Schemas an das Modell
    übergeben werden. Die Umgebungs-Überschreibung `RAG_TOOLS_ENABLED` erwartet
    eine mit `||` getrennte Liste.

## Die fünf eingebauten Tools

| Tool | Was es tut | Zitate |
|---|---|---|
| `search` | Semantische top-k-Suche | ja |
| `list_documents` | Wissensbasis auflisten | nein (navigierend) |
| `fetch_document` | Ein komplettes Dokument in Lesereihenfolge laden | ja |
| `expand_context` | Nachbarabschnitte um einen Treffer | ja |
| `verify_claim` | Belege für eine geplante Aussage erneut abrufen | ja |

### `search`

Semantische top-k-Suche — das ursprüngliche einzelne Tool. Parameter: `query`,
`top_k` sowie optional `document` (ein **exakter** `source_file`), um die Suche
auf ein Dokument einzuschränken. Diese Einschränkung erfordert
`retrieval.filterable_fields: [source_file]`, sonst wird der Filter ignoriert.
Aus Gründen der Rückwärtskompatibilität kommen Funktionsname und Beschreibungen
aus dem `tool:`-Block, nicht aus `tools.descriptions`.

### `list_documents`

Listet jedes Dokument der Collection auf: exakter `source_file`, Titel, Anzahl
der Chunks und eine ungefähre Tokenzahl. Das Tool hat keine Parameter und ist
rein **navigierend** — es liefert keine Zitate. Es ist genau das, womit das
Modell einen unscharfen Verweis („das Kage-2018-Paper") in den exakten
`source_file` auflöst, den `fetch_document` und `expand_context` benötigen, und
es beantwortet direkt die Frage „welche Dokumente sind in der Wissensbasis?".

### `fetch_document`

Lädt ein **komplettes** Dokument, alle Abschnitte in Lesereihenfolge, anhand
seines exakten `source_file`. Das ist das richtige Tool für Zusammenfassungen und
Überblicke, wo eine semantische top-k-Suche immer nur verstreute Fragmente
liefert. Das Ergebnis ist durch `tools.fetch_max_chunks` begrenzt, und das
Payload setzt `truncated: true`, wenn die Grenze erreicht wurde — so weiß das
Modell, dass es nicht den gesamten Text gesehen hat.

### `expand_context`

Gibt die Abschnitte innerhalb von ±`window` um einen `section_index` in einem
Dokument zurück (`source_file`, `section_index`, `window`). Nützlich gegen den
klassischen RAG-Fehler „der Chunk war zu klein": das Modell hat einen
vielversprechenden, aber abgeschnitten wirkenden Treffer und holt dessen
Nachbarn, statt zu raten.

### `verify_claim`

Ein Halluzinations-Schutz. Das Modell übergibt eine Aussage, die es machen will;
das Tool fragt die Wissensbasis erneut ab und liefert die Belegstellen plus ein
`supported`-Signal, sodass eine ungestützte Behauptung verworfen oder
abgeschwächt werden kann, bevor sie den Nutzer erreicht.

## Beschreibungen sind der Prompt

Die Beschreibung eines Tools ist das Einzige, was das Modell darüber weiß — also
der wichtigste Stellhebel. Alle eingebauten Tools bringen sprachabhängige
Standardtexte auf Deutsch und Englisch mit, ausgewählt über das Top-Level-Feld
`language:` deiner Konfiguration (siehe [Konfiguration](configuration.md)).
Überschreibe sie pro Tool-ID unter `tools.descriptions` — sinnvoll, um das
Vokabular deiner Domäne zu sprechen („Paper", „Baustein", „Ticket") statt des
generischen „Dokument".

## Ein eigenes Tool schreiben

Ein Tool besteht aus einem Schema-Builder (liefert ein OpenAI-Function-Schema als
`dict`) und einem async Handler `(args: dict, ctx: ToolContext) -> ToolResult`.
Die Typen liegen in [`tools/base.py`](https://github.com/aihpi/pilotprojekt-rag-template/blob/main/apps/chainlit/tools/base.py).

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
            "description": "Seitenzahl eines Dokuments, über seinen exakten source_file.",
            "parameters": {
                "type": "object",
                "properties": {"source_file": {"type": "string"}},
                "required": ["source_file"],
            },
        },
    }


@register_tool("count_pages", build_schema=_schema)
async def _count_pages(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    from rag_tool import fetch_document          # im Handler — siehe unten

    results = await fetch_document(
        str(args.get("source_file") or ""),
        collection=ctx.collection,
        max_chunks=ctx.fetch_max_chunks,
    )
    pages = {(r.metadata or {}).get("page_start") for r in results} - {None}
    return ToolResult(payload={"pages": len(pages)}, results=[])
```

Importiere das Modul unten in `tools/__init__.py`, damit der Decorator läuft, und
trage die ID dann in `tools.enabled` ein.

!!! warning "Zwei Regeln, die dich sonst einholen"
    **Zitate entstehen aus `results`.** `ToolResult.results` muss
    RagResult-förmig sein (`.text`, `.score`, `.metadata`) — diese Elemente
    speisen die Aggregation und das Zitat-Panel. Ein rein navigierendes Tool gibt
    `results=[]` zurück; nur das `payload` liest das Modell.

    **Importiere `rag_tool` im Handler-Body**, niemals am Modulanfang. `tools`
    muss frei von der Kette `rag_tool → settings → get_config()` bleiben, weil
    `settings` die Konfiguration beim Import aufbaut — ein Top-Level-Import
    erzeugt einen Zyklus.

## Ungültige IDs schlagen sofort fehl

Eine unbekannte ID in `tools.enabled` wird schon beim Laden der Konfiguration
abgewiesen, und der Validator listet die registrierten IDs in der Fehlermeldung.
Ein Tippfehler zeigt sich damit beim Start und nicht als still fehlende
Fähigkeit zur Anfragezeit.
