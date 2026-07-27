# Abbildungen & Bilder

Ohne diese Funktion **verwirft der PDF-Parser Abbildungen komplett** — nur ihre
Bildunterschriften überleben, als Text. Bei Papern und Handbüchern, deren
Argumentation in einem Diagramm steckt, ist das ein echter Verlust. Mit dem
`images:`-Block werden Abbildungen zu vollwertigem, durchsuchbarem Inhalt.

```yaml
images:
  mode: none            # none | describe | attach
  vision_model: gemma-4-31b
  images_scale: 2.0     # Docling-Renderskalierung (2.0 = höher aufgelöste Ausschnitte)
  inline_figures: true
  show_unmarked_figures: true
  inline_figure_caption: false
  max_attach_images: 3
  attach_image_max_px: 768
  vision_capable_models: [gemma-4-31b]
```

Der Modus lässt sich ohne Änderung der YAML über die Umgebungsvariable
`IMAGES_MODE` umschalten — praktisch für A/B-Vergleiche auf demselben Korpus.

## Drei Modi

| Modus | Beim Ingest | Zur Anfragezeit |
|---|---|---|
| `none` | Abbildungen ignoriert | — (die billigste Variante) |
| `describe` | Jede Abbildung wird gerendert und von `vision_model` beschrieben, als eigener Chunk gespeichert | Die Beschreibung wird wie jeder Text-Chunk durchsucht und zitiert |
| `attach` | Wie `describe` | Zusätzlich: die Bild-**Pixel** gehen an ein vision-fähiges Chat-Modell |

In `describe` wird jede Abbildung zu einem normalen, durchsuchbaren und
zitierbaren Chunk, dessen Text die vom Modell geschriebene Beschreibung ist — in
der Sprache der Instanz. Die Metadaten führen `is_figure`, `figure_index`,
`image_path` und die Seite. Da das Retrieval danach nur noch Text berührt,
funktioniert `describe` anschließend mit **jedem** Chat-Modell — das
Vision-Modell wird beim Ingest gebraucht, nicht zur Anfragezeit.

`attach` baut auf genau demselben Ingest auf und ergänzt einen Vision-Durchgang
für die Antwort.

## Kosten und wann ein Re-Ingest nötig ist

Die Regel ist einfach: **ein Vision-Aufruf pro Abbildung, beim Ingest.** In
`describe` entstehen pro Anfrage keine weiteren Kosten.

!!! warning "Moduswechsel"
    Der Wechsel von `none` zu `describe` oder `attach` erfordert einen
    **Re-Ingest** — die Abbildungen wurden nie gerendert oder beschrieben. Der
    Wechsel zwischen `describe` und `attach` **nicht**: Bilder und Beschreibungen
    sind bereits gespeichert, es ändert sich nur das Verhalten zur Anfragezeit.
    Die Flags dafür stehen unter [Daten hinzufügen](adding-data.md).

## Abbildungen in der Antwort anzeigen

Zwei unabhängige Schalter entscheiden, was der Nutzer tatsächlich sieht:

| `inline_figures` | `show_unmarked_figures` | Ergebnis |
|---|---|---|
| `true` | `true` | Markierte Abbildung erscheint **über** dem Absatz, der sie beschreibt; alle übrigen abgerufenen Abbildungen als Vorschaubilder unter der Antwort (Default) |
| `true` | `false` | Nur Abbildungen, die das Modell wirklich markiert hat — am saubersten, aber es erscheint nichts, wenn es den Marker vergisst |
| `false` | `true` | Keine Inline-Bilder; jede abgerufene Abbildung als Vorschaubild unter der Antwort |
| `false` | `false` | Überhaupt keine Bildanzeige — Beschreibungen bleiben durchsuchbar und zitierbar |

`inline_figure_caption: true` gibt zusätzlich die Bildunterschrift als kursive
Zeile unter dem eingebetteten Bild aus.

## Wie der Abbildungs-Marker funktioniert

Lohnt sich zu verstehen, weil es sowohl die guten als auch die merkwürdigen Fälle
erklärt:

1. Der Retrieval-Kontext enthält pro Abbildung eine zusätzliche Zeile:
   `Abbildungs-Marker: {{ABB:<dateiname>}}`.
2. Eine System-Anweisung pro Anfrage bittet das Modell, diesen Marker unverändert
   in eine eigene Zeile direkt vor den Absatz zu setzen, der die Abbildung
   beschreibt.
3. Die Nachbearbeitung der Antwort ersetzt den Marker durch das eigentliche Bild.

Nicht auflösbare Marker werden still entfernt — Nutzer sehen nie ein rohes
`{{ABB:…}}` — und die Abbildung erscheint dann einfach unten. Der Anweisungstext
selbst ist über `images.figure_marker_prompt` konfigurierbar.

## Wo die Bilder liegen

Abbildungen werden als PNG unter `<sources.data_dir>/figures/` abgelegt (Pfad
über `images.figure_store_dir` überschreibbar) und über eine
**authentifizierte** Route ausgeliefert: `/sources/figure/<datei>` — dieselben
Zugriffsregeln wie für deine Quelldokumente. Der Ordner steht absichtlich in
`.gitignore`: er wird bei jedem Ingest neu erzeugt und gehört nicht ins
Repository.

## `attach` braucht ein vision-fähiges Chat-Modell

Das Gateway liefert kein Fähigkeits-Flag pro Modell, deshalb ist
`images.vision_capable_models` maßgeblich. Steht das aktive Chat-Modell nicht auf
dieser Liste, fällt die App still auf die gewöhnliche Textantwort zurück und
loggt einen Hinweis — die Antwort ist weiterhin korrekt, nur ohne Pixel.

Vor dem Aufruf wird jede Abbildung so verkleinert, dass ihre längste Seite höchstens
`attach_image_max_px` beträgt, und als JPEG gesendet. Das nicht entfernen:
Abbildungen in Originalauflösung lassen Gateways die Anfrage mit **HTTP 413**
ablehnen. `max_attach_images` begrenzt, wie viele Abbildungen eine einzelne
Antwort mitführen darf.

!!! note "Eine kosmetische Eigenheit"
    Chainlit rendert Markdown-Bilder in einem 16:9-Rahmen mit begrenzter Breite.
    Hohe Abbildungen (Hochformat-Diagramme, gestapelte Plots) bekommen daher
    seitliche Ränder. Es wird nichts abgeschnitten — ein Klick auf das Bild
    öffnet es weiterhin vollständig.
