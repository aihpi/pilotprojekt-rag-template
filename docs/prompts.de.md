# System-Prompts

Der System-Prompt ist die feste Anweisung, an die sich der Assistent in jedem
Gespräch hält. Er macht aus einer Suchmaschine *deinen* Assistenten: Er sagt, wer
der Assistent ist, verlangt, dass er erst nachschlägt und dann antwortet, und
legt die genaue Schreibweise von Quellen und Anschlussfragen fest, damit die App
daraus klickbare Links und Buttons machen kann.

Du kannst ihn selbst schreiben oder die App einen aus deinen eigenen Dokumenten
schreiben lassen.

## Woher der Prompt kommt

Beim Start schaut die App an drei Stellen nach, in dieser Reihenfolge, und nimmt
die erste, die etwas liefert:

| Reihenfolge | Quelle | Wann sie greift |
|---|---|---|
| 1 | `prompt.system_prompt_path` | Du hast eine Datei angegeben **und** diese Datei existiert |
| 2 | Generierung | `prompt.auto_generate: true` (Standard): die App schreibt beim Start einen |
| 3 | `apps/chainlit/config/prompts/default_system.md` | Der mitgelieferte, falls keine der beiden Möglichkeiten etwas ergeben hat |

Wegen dieser Reihenfolge schaltet eine eigene Datei das automatische Schreiben
komplett ab. Deine Datei gewinnt immer.

```yaml
prompt:
  auto_generate: true
  template_path: ../../system.md      # optional
  sample_size: 40
  starter_questions: ["...", "..."]
  # system_prompt_path: ./my_prompt.md  # explizit -> keine Generierung
```

## Generierung

Die App rät nicht, worum es in deinen Dokumenten geht. Sie liest sie:

1. Sie nimmt eine Stichprobe des **tatsächlich gespeicherten** Texts deiner
   Dokumente: die Abschnittsüberschriften jedes Dokuments plus einige typische
   Ausschnitte. Wie viel verwendet wird, steuert `prompt.sample_size`
   (Standard `40`).
2. Sie lädt ein Gerüst, das Struktur und Strenge vorgibt, aber keine
   Formulierungen zu deinem Fachgebiet. Standardmäßig ist das eine `system.md`
   neben dem Projekt, falls vorhanden, sonst die mitgelieferte. Mit
   `prompt.template_path` zeigst du woanders hin.
3. Sie lässt dein Chat-Modell aus beidem die Anweisung schreiben.

Das Ergebnis wird neben deiner Einstellungsdatei als
`.generated_system_prompt.<collection>.md` gespeichert und bei jedem Neustart
wiederverwendet. Das kostet also einen Modellaufruf pro Satz Dokumente, nicht
einen pro Start.

Die geschriebene Anweisung deckt ab:

- wer der Assistent ist, aus deinen Dokumenten abgeleitet;
- die Pflicht, erst nachzuschlagen und nur aus dem Gefundenen zu antworten;
- das genaue Format für Quellen;
- die Sprache der Antwort;
- eine Höchstlänge;
- das Format der Anschlussfragen.

Für eine neue Anweisung die gespeicherte Datei löschen oder die Einstellung
`REGENERATE_SYSTEM_PROMPT` nutzen:

```bash
rm .generated_system_prompt.papers.md
# oder:
REGENERATE_SYSTEM_PROMPT=true chainlit run app.py
```

!!! warning "Zitate und Anschlussfragen werden über deutsche Marker geparst"
    Die App erkennt Quellen und Anschlussfragen nur an ihrer **deutschen**
    Schreibweise: Quellen als `Quelle N: <Abschnitt> (S.<Seite>)` und
    Anschlussfragen unter der Überschrift `Anschlussfragen:`. Wenn klickbare
    Quellen und Anschlussfragen-Buttons funktionieren sollen, setze also
    `language: de`.

    Deine **Dokumente** dürfen jede Sprache haben. Das Modell liest problemlos
    englische Paper und antwortet auf Deutsch. Wenn du die Anweisung selbst
    schreibst, übernimm diese beiden Formulierungen exakt, sonst erscheinen Links
    und Buttons nicht mehr.

## Prompt im Betrieb ansehen und bearbeiten

Das Zahnrad-Panel zeigt die aktive Anweisung in einem bearbeitbaren Feld
(„System-Prompt (bearbeitbar)"). Änderungen dort gelten **nur für dich und nur in
dieser Sitzung**. Leerst du das Feld, gilt wieder die normale Anweisung.

Damit eignet sich das Panel bestens zum Ausprobieren von Formulierungen, aber
nicht dazu, eine Änderung für alle auszurollen. Für etwas Dauerhaftes bearbeite
die Prompt-Datei oder lass `prompt.system_prompt_path` auf eine eigene Datei
zeigen.

## Chat-Modell wählen

Im selben Zahnrad-Panel sitzt eine Modellauswahl. Sie füllt sich mit dem, was
dein KI-Dienst anbietet (Suchmodelle werden ausgeblendet, die können nicht
chatten). Manche Dienste veröffentlichen keine Liste, dann bleibt die Auswahl
leer. In dem Fall trägst du die Namen selbst ein:

```yaml
models:
  chat_model: gpt-oss-120b
  selectable_chat_models: []   # wird mit dem gemergt, was /v1/models meldet
```

Die Auswahl jeder Person wird gespeichert und gilt damit auch für deren neue
Chats.

!!! note "Verwandte Seiten"
    [Erste Schritte](getting-started.md) für den ersten Start,
    [Konfigurationsreferenz](configuration.md) für alle `prompt:`-Felder und
    [Agentische Tools](tools.md) für die Fähigkeiten, die die Anweisung nutzen
    lässt.
