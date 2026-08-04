# Dokumente ändern

Wenn der Assistent läuft, willst du irgendwann Dokumente ergänzen, eines
korrigieren oder eines herausnehmen. Das geht immer auf dieselbe einfache Weise:

**Ordner ändern, dann die App neu starten.**

Der Ordner entscheidet, was der Assistent weiß. Es gibt nichts anderes, das du
gleichhalten musst, und an den Einstellungen musst du nichts anfassen.

## Der Ordner, auf den es ankommt

Deine Dokumente liegen in einem einzigen Ordner:

```
apps/chainlit/data/documents/
```

Dateien hineinlegen, herausnehmen, austauschen. Das ist alles.

## Ein Dokument hinzufügen

1. Kopiere die Datei nach `apps/chainlit/data/documents/`.
2. Führe im Ordner `apps/chainlit` das hier aus:

```bash
docker compose up -d
```

Gelesen wird nur die neue Datei. Alles, was schon da ist, bleibt unberührt. Du
wartest also nicht darauf und bezahlst es nicht ein zweites Mal.

## Ein Dokument korrigieren

Ersetze die Datei durch die korrigierte Fassung und führe denselben Befehl aus. Die
App merkt, dass sich der Inhalt geändert hat, und liest sie von selbst neu ein. Du
musst ihr nichts sagen.

Eine Datei umzubenennen zählt ebenfalls als Änderung: die alte Fassung wird
herausgenommen, der neue Name wird eingelesen.

## Ein Dokument entfernen

Lösche die Datei aus dem Ordner und führe denselben Befehl aus. Ihr Inhalt wird aus
dem Assistenten entfernt und taucht damit nicht mehr in Antworten auf.

Das ist wichtiger, als es klingt. Ohne diesen Schritt würde der Assistent weiter aus
einem gelöschten Dokument antworten, und der Quellenverweis unter der Antwort würde
ins Leere führen.

## Alles auf einmal austauschen

Du kannst auch den kompletten Dokumentenbestand in einem Zug tauschen: alte Dateien
löschen, neue hineinlegen, den Befehl einmal ausführen. Die alten Inhalte werden
entfernt und die neuen im selben Lauf eingelesen.

## Muss ich irgendetwas neu starten?

Nein. Der Befehl oben macht alles, und das Chat-Fenster funktioniert währenddessen
weiter. Du musst nichts schließen, und wer den Assistenten benutzt, muss die Seite
für spätere Fragen nicht neu laden.

## Woran sehe ich, dass es geklappt hat?

Der Lauf sagt es in einfachen Zahlen. Ein neues Dokument von neun einlesen sieht so
aus:

```
[ingest] 1 file(s) to ingest, 8 unchanged and skipped.
Ingested 49 chunks into 'papers'.
```

Eines entfernen sieht so aus:

```
[ingest] removed 48 entr(ies) for deleted document data/documents/Choi_2019.pdf
```

Und wenn es wirklich nichts zu tun gibt:

```
[ingest] nothing to do: all 9 file(s) are already indexed and unchanged.
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
