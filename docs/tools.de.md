# Agentische Tools

Ein einfacher Dokumentenassistent sucht einmal und antwortet aus dem Ergebnis.
Dieser bekommt stattdessen einen **Satz von Fähigkeiten** und entscheidet selbst,
welche er nutzt, wie oft und in welcher Reihenfolge: auflisten, welche Dokumente
es gibt, ein ganzes Dokument lesen, mehr Text um eine vielversprechende Stelle
holen oder eine Aussage prüfen, bevor er sie trifft.

Du legst fest, welche Fähigkeiten er bekommt. Jede ist eine kleine Datei in
`apps/chainlit/tools/`.

```yaml
tools:
  enabled: [search, list_documents, fetch_document, expand_context, verify_claim]
  descriptions:
    list_documents: "eigene Beschreibung, genau das liest das Modell"
  fetch_max_chunks: 200   # Obergrenze für die Dokumentgröße bei fetch_document
  expand_window: 1        # Standard-Nachbarfenster für expand_context
```

!!! note "Der Default ist Ein-Tool-RAG"
    Sagst du nichts, bekommt der Assistent nur `search`, eine ältere Einrichtung
    verhält sich also exakt wie bisher. Die Reihenfolge, in der du sie aufzählst,
    ist die Reihenfolge, in der das Modell von ihnen erfährt. Es gibt außerdem die
    Einstellung `RAG_TOOLS_ENABLED`, die die Namen mit `||` getrennt erwartet.

## Die fünf eingebauten Tools

| Tool | Was es tut | Liefert Quellen |
|---|---|---|
| `search` | Findet die Stellen, die am besten zur Frage passen | ja |
| `list_documents` | Listet auf, was in der Wissensbasis liegt | nein (nur zur Orientierung) |
| `fetch_document` | Liest ein ganzes Dokument von vorne bis hinten | ja |
| `expand_context` | Holt den Text rund um eine Stelle | ja |
| `verify_claim` | Prüft eine Aussage an den Dokumenten, bevor sie gesagt wird | ja |

### `search`

Die gewöhnliche Suche, und das Einzige, was ältere Einrichtungen hatten. Sie
nimmt eine Frage (`query`), eine Trefferzahl (`top_k`) und optional ein einzelnes
`document`, in dem gesucht werden soll. Letzteres funktioniert nur, wenn du das
Filtern nach Dateinamen erlaubst, über
`retrieval.filterable_fields: [source_file]`, sonst wird es stillschweigend
ignoriert.

Aus Kompatibilität mit älteren Einrichtungen nimmt ausgerechnet dieses Tool seine
Texte aus dem `tool:`-Block statt aus `tools.descriptions`.

### `list_documents`

Listet jedes Dokument auf: den exakten Dateinamen, den Titel, in wie viele Stücke
es zerteilt wurde und ungefähr wie lang es ist. Es braucht keine Eingabe und
liefert keine Quellen, denn es dient nur der Orientierung.

Es löst ein konkretes Problem: Menschen verweisen unscharf auf Dokumente („das
Kage-2018-Paper"), aber `fetch_document` und `expand_context` brauchen den
exakten Dateinamen. Damit kann der Assistent ihn nachschlagen. Außerdem
beantwortet es direkt die Frage „welche Dokumente hast du?".

### `fetch_document`

Liest ein **komplettes** Dokument, alle Abschnitte in der richtigen Reihenfolge,
anhand des exakten Dateinamens. Das ist die richtige Wahl für Zusammenfassungen
und Überblicke, wo die gewöhnliche Suche immer nur verstreute Fragmente liefert
und dem Assistenten das halbe Dokument entginge.

Sehr lange Dokumente werden bei `tools.fetch_max_chunks` abgeschnitten, und der
Assistent erfährt, dass das passiert ist. So weiß er, dass er nicht alles gesehen
hat.

### `expand_context`

Holt die Abschnitte direkt vor und nach einem bestimmten. Hilft gegen das
klassische Problem, dass eine gefundene Stelle zu kurz ist: Der Assistent sieht
etwas Vielversprechendes, das mitten im Gedanken abzubrechen scheint, und holt
den Text drumherum, statt zu raten.

### `verify_claim`

Ein Schutz gegen erfundene Antworten. Der Assistent übergibt einen Satz, den er
gleich schreiben will, und hier wird noch einmal in den Dokumenten nach Belegen
gesucht. Zurück kommt, ob der Satz tatsächlich gedeckt ist, sodass eine
ungedeckte Behauptung verworfen oder abgeschwächt werden kann, bevor sie jemand
zu sehen bekommt.

## Beschreibungen sind der Prompt

Die Beschreibung einer Fähigkeit ist das Einzige, was das Modell darüber weiß,
und damit der wichtigste Stellhebel. Alle eingebauten bringen sinnvolle Texte auf
Deutsch und Englisch mit, ausgewählt über die Einstellung `language:` (siehe
[Konfiguration](configuration.md)).

Überschreibe sie unter `tools.descriptions`. Das lohnt sich, um dein eigenes
Vokabular zu nutzen: „Paper", „Baustein" oder „Ticket" statt des generischen
„Dokument".

## Ein eigenes Tool schreiben

Dieser Teil braucht Python. Eine Fähigkeit besteht aus zwei Teilen: einer
Beschreibung dessen, was sie als Eingabe erwartet, und einer Funktion, die die
Arbeit macht. Die Typen liegen in
[`tools/base.py`](https://github.com/aihpi/pilotprojekt-rag-template/blob/main/apps/chainlit/tools/base.py).

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
    from rag_tool import fetch_document          # im Handler, siehe unten

    results = await fetch_document(
        str(args.get("source_file") or ""),
        collection=ctx.collection,
        max_chunks=ctx.fetch_max_chunks,
    )
    pages = {(r.metadata or {}).get("page_start") for r in results} - {None}
    return ToolResult(payload={"pages": len(pages)}, results=[])
```

Trage das Modul unten in `tools/__init__.py` in die Import-Liste ein, damit es
registriert wird, und dann den Namen in `tools.enabled`.

!!! warning "Zwei Regeln, die dich sonst einholen"
    **Quellen entstehen aus `results`.** Was du in `ToolResult.results` legst,
    muss `.text`, `.score` und `.metadata` haben, denn daraus wird die
    Quellenliste gebaut. Eine Fähigkeit, die nur der Orientierung dient, gibt
    `results=[]` zurück, dann erreicht nur das `payload` das Modell.

    **Importiere `rag_tool` innerhalb der Funktion, nie am Dateianfang.** Die
    Einstellungen werden beim Import geladen. Ein Import ganz oben lässt deshalb
    zwei Module aufeinander warten und die App startet nicht.

## Ungültige IDs schlagen sofort fehl

Ein Name in `tools.enabled`, den es nicht gibt, wird schon beim Lesen der
Einstellungen abgewiesen, und die Fehlermeldung listet die gültigen Namen auf.
Ein Tippfehler fällt damit sofort beim Start auf, statt den Assistenten still und
leise ohne eine Fähigkeit dastehen zu lassen, die du ihm zugedacht hattest.
