# Dokumente ändern

Wenn der Assistent läuft, willst du irgendwann Dokumente ergänzen, eines
korrigieren oder eines herausnehmen. Das geht immer auf dieselbe einfache Weise:

**Ordner ändern. Das ist alles.**

Die App behält deinen Dokumentenordner im Auge und übernimmt von selbst innerhalb
weniger Sekunden, was du hinzufügst, änderst oder löschst. Du musst keinen Befehl
ausführen und nichts neu starten.

## Der Ordner, auf den es ankommt

Deine Dokumente liegen in einem einzigen Ordner:

```
apps/chainlit/data/documents/
```

Dateien hineinlegen, herausnehmen, austauschen. Das ist alles.

## Ein Dokument hinzufügen

Kopiere die Datei nach `apps/chainlit/data/documents/`. Wenige Sekunden später ist
sie eingelesen und du kannst dazu Fragen stellen.

Gelesen wird nur die neue Datei. Alles, was schon da ist, bleibt unberührt. Du
wartest also nicht darauf und bezahlst es nicht ein zweites Mal.

Eine große Datei, die noch kopiert wird, bleibt liegen, bis das Kopieren fertig ist.
So wird sie nie halb eingelesen.

## Ein Dokument korrigieren

Ersetze die Datei durch die korrigierte Fassung. Die App merkt, dass sich der Inhalt
geändert hat, und liest sie von selbst neu ein. Du musst ihr nichts sagen.

Eine Datei umzubenennen zählt ebenfalls als Änderung: die alte Fassung wird
herausgenommen, der neue Name wird eingelesen.

## Ein Dokument entfernen

Lösche die Datei aus dem Ordner. Ihr Inhalt wird aus dem Assistenten entfernt und
taucht damit nicht mehr in Antworten auf.

Das ist wichtiger, als es klingt. Ohne diesen Schritt würde der Assistent weiter aus
einem gelöschten Dokument antworten, und der Quellenverweis unter der Antwort würde
ins Leere führen.

## Alles auf einmal austauschen

Du kannst auch den kompletten Dokumentenbestand in einem Zug tauschen: alte Dateien
löschen, neue hineinlegen. Die alten Inhalte werden entfernt und die neuen zusammen
damit eingelesen.

## Muss ich etwas ausführen oder neu starten?

Nein, beides nicht. Das Chat-Fenster funktioniert die ganze Zeit weiter, und wer den
Assistenten benutzt, muss die Seite für spätere Fragen nicht neu laden.

Wenn du es lieber selbst machst, kannst du das. Schreibe `DOCUMENT_WATCH=false` in
deine `.env`-Datei, dann wird nichts mehr automatisch übernommen. Dieser Befehl liest
dann ein, was sich geändert hat, wann du willst:

```bash
docker compose up -d
```

## Woran sehe ich, dass es geklappt hat?

**Im Chat-Fenster.** Unten rechts in der Ecke sitzt eine kleine Anzeige. Sie ist
blass und hält sich zurück; fahre mit der Maus darüber, um zu lesen, was gerade
passiert.

- ein ruhiger Punkt heißt, es ist nichts im Gange, und beim Darüberfahren siehst du
  die letzte Änderung
- ein drehender Kreis mit kurzem Text heißt, es arbeitet gerade
- ein grüner Haken heißt, es ist fertig

**Im Protokoll**, wenn du die Details willst:

```bash
docker compose logs -f chainlit
```

Ein Dokument hinzufügen sieht so aus:

```
[watch] documents changed (new: 1, edited: 0, removed: 0); indexing
[watch]   new: data/documents/Choi_2019.pdf
[watch] done: 48 chunk(s) indexed
```

Eines entfernen sieht so aus:

```
[watch] documents changed (new: 0, edited: 0, removed: 1); indexing
[watch] done: 49 entr(ies) removed
```

Öffne danach den Chat, stelle eine Frage, die nur das geänderte Dokument beantworten
kann, und klicke auf die Quelle unter der Antwort. Öffnet sich das richtige PDF, ist
alles in Ordnung.

## Zwei Dinge, auf die du achten solltest

!!! danger "Jedes Dokument braucht seinen eigenen Dateinamen"
    Dokumente werden über den **Dateinamen** erkannt, nicht über den Ordner, in dem
    sie liegen. Zwei Dateien, die beide `intro.pdf` heißen, gelten als dasselbe
    Dokument. Nur eine davon ist durchsuchbar, die andere geht verloren.

    Ein Lauf warnt dich, wenn ein Name doppelt vorkommt. Benenne die Dateien dann
    eindeutig um und lies alles noch einmal ein (siehe unten).

!!! warning "Ein leerer Ordner wird als Versehen behandelt"
    Ist der Ordner völlig leer, wird **nichts gelöscht**. Das ist Absicht: ein leerer
    Ordner liegt fast immer an einem technischen Problem und nicht daran, dass jemand
    alles wegwerfen wollte. Das Gedächtnis des Assistenten deswegen stillschweigend
    zu leeren wäre schlimmer, als nichts zu tun. Du bekommst einen entsprechenden
    Hinweis.

    Um absichtlich zu leeren, nimm den vollständigen Neuaufbau unten.

## Wann ein vollständiger Neuaufbau nötig ist

Manche Änderungen betreffen **alle** Dokumente, nicht nur die, die du angefasst
hast. Dann muss alles neu eingelesen werden:

```bash
docker compose run --rm ingest python -m kb.ingest --recreate
docker compose up -d
```

Das brauchst du, nachdem du geändert hast, wie Dokumente in Stücke zerlegt werden
(`chunking`), nachdem du die Bildverarbeitung eingeschaltet hast (`images.mode`),
oder nachdem du das Suchmodell gewechselt hast (`embed_model`, was sonst abgelehnt
wird, weil sich alte und neue Daten nicht vergleichen lassen).

!!! warning "Ein Neuaufbau kostet wieder Geld"
    Jedes Dokument wird von vorn verarbeitet, und mit `images.mode: describe` wird
    jedes Bild erneut beschrieben, ein KI-Aufruf pro Bild. Der Beispielbestand aus
    neun Papern hat rund 170 Bilder. Ein normaler Lauf, der nur Geändertes anfasst,
    ist deutlich günstiger.

## Wo die Details stehen

- [Daten hinzufügen](adding-data.de.md) behandelt Dateiformate über PDF hinaus und
  wie Dokumente in Stücke zerlegt werden.
- [Abbildungen](images.de.md) behandelt, was mit Bildern und Diagrammen passiert.
