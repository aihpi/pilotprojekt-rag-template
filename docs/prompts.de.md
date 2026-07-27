# System-Prompts

Der System-Prompt macht aus einer Retrieval-Pipeline *deinen* Assistenten: Er legt
die Identität fest, erzwingt Retrieval vor der Antwort und fixiert das Zitat- und
Anschlussfragen-Format, das die UI parst. Du kannst ihn selbst schreiben — oder
die App beim Start einen aus deinem Korpus generieren lassen.

## Woher der Prompt kommt

Beim Start löst die App den Prompt aus drei Quellen auf, in dieser Reihenfolge:

| Reihenfolge | Quelle | Wann sie greift |
|---|---|---|
| 1 | `prompt.system_prompt_path` | Das Feld ist gesetzt **und** die Datei existiert |
| 2 | Generierung | `prompt.auto_generate: true` (Default) — beim Start wird ein Prompt generiert |
| 3 | `apps/chainlit/config/prompts/default_system.md` | Mitgelieferter Fallback, wenn keine der beiden Optionen einen Prompt liefert |

`prompt.system_prompt_path` deaktiviert die Generierung also vollständig — eine
explizite Datei gewinnt immer.

```yaml
prompt:
  auto_generate: true
  template_path: ../../system.md      # optional
  sample_size: 40
  starter_questions: ["...", "..."]
  # system_prompt_path: ./my_prompt.md  # explizit -> keine Generierung
```

## Generierung

Die Generierung (`apps/chainlit/system_prompt_gen.py`) rät nicht, worum es in
deinem Korpus geht — sie liest ihn:

1. Sie sampelt die **tatsächlich indexierten** Chunks der aktiven Collection
   (Abschnittstitel pro Dokument plus einige repräsentative Ausschnitte). Wie
   viele verwendet werden, steuert `prompt.sample_size` (Default `40`).
2. Sie lädt eine Struktur-Vorlage — `prompt.template_path`, standardmäßig eine
   `system.md` im Repo-Root, falls vorhanden, sonst der mitgelieferte Fallback.
   Die Vorlage liefert Struktur und Strenge, nicht die Domänenformulierungen.
3. Sie lässt das konfigurierte Chat-Modell daraus einen Prompt schreiben.

Das Ergebnis wird als `.generated_system_prompt.<collection>.md` neben der Config
gecacht (gitignored) und bei jedem Neustart wiederverwendet — die Generierung
kostet also einen Modellaufruf pro Korpus, nicht einen pro Start.

Der generierte Prompt enthält:

- eine domänenspezifische Identität, aus dem Korpus abgeleitet;
- die Pflicht zum Retrieval-First (Tool aufrufen, nur aus den gefundenen Passagen antworten);
- das Zitierformat, das die App parst;
- die Antwortsprache;
- ein Längenlimit;
- das Format der Anschlussfragen.

Für eine neue Generierung die Cache-Datei löschen oder
`REGENERATE_SYSTEM_PROMPT=true` setzen:

```bash
rm .generated_system_prompt.papers.md
# oder:
REGENERATE_SYSTEM_PROMPT=true chainlit run app.py
```

!!! warning "Zitate und Anschlussfragen werden über deutsche Marker geparst"
    Die App erkennt Zitate und Anschlussfragen derzeit an ihren **deutschen**
    Markern: Zitate als `Quelle N: <Abschnitt> (S.<Seite>)`, Anschlussfragen unter
    der Überschrift `Anschlussfragen:`. Wenn klickbare Zitate und
    Anschlussfragen-Buttons funktionieren sollen, setze also `language: de`. Das
    **Korpus** darf jede Sprache haben — das Modell liest die englischen Paper und
    antwortet deutsch. Wenn du den Prompt selbst schreibst, übernimm diese beiden
    Marker exakt.

## Prompt im Betrieb ansehen und bearbeiten

Das Zahnrad-Panel zeigt den aktiven Prompt in einem bearbeitbaren Feld
(„System-Prompt (bearbeitbar)"). Eine Bearbeitung dort gilt **pro Nutzer/Session**
— sie wird als `custom_prompt` gespeichert und überschreibt den geladenen Prompt.
Das Feld leeren führt zurück zum Standard.

Damit eignet sich das Panel bestens zum Iterieren an Formulierungen; ein
Deployment-Mechanismus ist es nicht. Für eine dauerhafte Änderung die
Prompt-Datei bearbeiten oder `prompt.system_prompt_path` auf eine eigene Datei
zeigen lassen.

## Chat-Modell wählen

Im selben Zahnrad-Panel sitzt ein Chat-Modell-Selektor. Seine Liste füllt sich
automatisch aus `/v1/models` des Gateways (Embedding-Modelle werden
herausgefiltert) und lässt sich über `models.selectable_chat_models` ergänzen —
nützlich, wenn das Gateway seine Modelle nicht aufzählt:

```yaml
models:
  chat_model: gpt-oss-120b
  selectable_chat_models: []   # wird mit dem gemergt, was /v1/models meldet
```

Die Auswahl wird pro Nutzer in der Datenbank gespeichert und gilt damit auch für
neue Chats.

!!! note "Verwandte Seiten"
    [Erste Schritte](getting-started.md) für den ersten Start,
    [Konfigurationsreferenz](configuration.md) für alle `prompt:`-Felder und
    [Agentische Tools](tools.md) für die Tools, die der Prompt aufrufen lässt.
