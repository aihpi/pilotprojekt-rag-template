# Wenn etwas nicht funktioniert

Fehler, die wirklich vorgekommen sind, mit Ursache und Lösung.

## „database disk image is malformed"

Die Datei mit dem Chat-Verlauf ist beschädigt. Deine Dokumente und der Suchindex
liegen getrennt davon und sind nicht betroffen, es geht also nichts von dem verloren,
was du eingelesen hast.

**Warum.** Der Chat-Verlauf ist eine SQLite-Datei. Sie lag früher im Ordner
`.chainlit`, der zwischen deinem Rechner und dem Container geteilt wird. SQLite
braucht genaue Dateisperren, um konsistent zu bleiben, und Docker Desktop kann diese
über die Grenze zwischen macOS oder Windows und dem Linux-Container nur nachahmen.
Ein Schreibvorgang, der im falschen Moment unterbrochen wird, etwa durch einen
Neustart der App, kann die Datei dann zerstören.

**Betroffen ist Docker Desktop unter macOS und Windows.** Unter Linux, und unter
Windows wenn das Projekt im WSL2-Dateisystem liegt, ist der Ordner ein normales
Linux-Dateisystem und das Problem tritt nicht auf.

**Für neue Installationen bereits behoben.** Die Datenbank liegt jetzt in einem
Docker-Volume, also einem echten Linux-Dateisystem, und ein vorhandener Verlauf wird
beim ersten Start automatisch dorthin übernommen. Du solltest das nicht wieder sehen.

**Wenn du es jetzt siehst**, rette die alten Nachrichten so, aus `apps/chainlit`:

```bash
docker compose stop chainlit
mv .chainlit/chat_history.sqlite3 .chainlit/chat_history.broken
sqlite3 .chainlit/chat_history.broken ".recover" | sqlite3 .chainlit/chat_history.sqlite3
docker compose up -d
```

Die dritte Zeile baut aus allem, was noch lesbar ist, eine gesunde Datei. Behalte die
`.broken`-Datei, bis du geprüft hast, dass dein Verlauf stimmt.

## Ein neues PDF erscheint nicht

Lege die Datei in `apps/chainlit/data/documents/` und warte einige Sekunden. Die App
beobachtet den Ordner und liest neue Dateien von selbst ein.

Wenn nichts passiert, prüfe der Reihe nach:

1. **Läuft die App?** `docker compose ps`
2. **Was sagt sie?** `docker compose logs -f chainlit` und nach `[watch]`-Zeilen
   schauen. Sie nennen jede Datei, die hinzugefügt, geändert oder entfernt wurde.
3. **Ist der Name eindeutig?** Dokumente werden allein über den Dateinamen erkannt.
   Eine zweite Datei namens `intro.pdf` gilt als dasselbe Dokument, und der Lauf warnt
   davor.
4. **Ist es der richtige Ordner?** Bei einer eigenen Einstellungsdatei entscheidet
   `data_sources[]`, welcher Ordner beobachtet wird, und Pfade zählen ab dem Ordner,
   in dem die Einstellungsdatei liegt.

Siehe [Dokumente ändern](managing-documents.de.md).

## „the 'tesseract' binary was not found"

Du hast `ocr: true` eingeschaltet. Das mitgelieferte Docker-Abbild enthält bewusst
kein Programm zur Texterkennung, damit es klein bleibt.

Die meisten PDFs brauchen das nicht: sie enthalten schon echten Text, und der
Standard `ocr: false` liest sie korrekt. Nur echte Scans, bei denen die Seite ein Foto
ist, brauchen OCR.

Wenn deine Dateien wirklich Scans sind, baue ein eigenes Abbild auf diesem auf und
installiere `tesseract-ocr` samt den benötigten Sprachen. Die Fehlermeldung nennt die
genauen Zeilen.

## Die App erreicht kein Modell auf meinem eigenen Rechner

Im Container bedeutet `localhost` den Container selbst, nicht deinen Rechner. Nimm
stattdessen `host.docker.internal`:

```
LITELLM_BASE_URL=http://host.docker.internal:11434/v1
```

Denk daran, dass die Modellnamen in der Einstellungsdatei von einem gehosteten Dienst
kommen und auf deinem eigenen Server nicht existieren. Die musst du also auch ändern.

## Port 8000 ist schon belegt

Etwas anderes auf deinem Rechner benutzt ihn. Wähle in `.env` einen anderen:

```
APP_PORT=8080
```

Dasselbe geht mit `QDRANT_PORT` und `POSTGRES_PORT`. Es ändert sich nur der Port auf
deinem Rechner, innerhalb der App musst du nichts anpassen.

## Der Assistent antwortet, aber es erscheinen keine Quellen

Quellenangaben und Anschlussfragen werden über deutsche Formulierungen erkannt und
funktionieren deshalb nur mit `language: de` in der Einstellungsdatei. Deine Dokumente
selbst können in jeder Sprache sein. Das ist eine bekannte Einschränkung.

## Hier steht nichts, was passt

Am schnellsten kommst du über das Protokoll weiter:

```bash
docker compose logs --tail 100 chainlit
```

Fehler beim Einlesen von Dokumenten beginnen mit `[ingest]`, Meldungen des
Ordner-Beobachters mit `[watch]`.
