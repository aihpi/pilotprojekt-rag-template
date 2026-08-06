# Antwortqualität prüfen

Du kannst in der `rag.config.yaml` die Chunking-Strategie, das Embedding-Modell
oder das Chat-Modell ändern. Das Schwierige ist zu wissen, ob die Änderung etwas
gebracht hat.

Die Evaluation gibt jeder Antwort zwei Kennzahlen und sammelt sie pro
Konfiguration. Damit wird aus „ist das besser?" etwas, das du anschauen kannst,
statt etwas, worüber man diskutiert.

Sie ist **standardmäßig aus**. Es wird nichts gemessen, gesendet oder gespeichert,
solange du sie nicht einschaltest.

## Die zwei Kennzahlen

**Faithfulness** (Belegtreue) fragt, ob die Aussagen der Antwort tatsächlich von
den abgerufenen Textstellen gedeckt sind. Ein niedriger Wert heißt: der Assistent
hat Dinge behauptet, die seine Quellen nicht hergeben.

**Relevance** (Relevanz) fragt, ob die Antwort die gestellte Frage beantwortet. Ein
niedriger Wert heißt: die Antwort kann vollkommen richtig sein und trotzdem am
Thema vorbeigehen.

Keine der beiden braucht eine von Hand geschriebene „richtige Antwort". Genau
deshalb funktionieren sie auf den echten Gesprächen, die sowieso schon stattfinden.

## Lies die Veränderung, nicht die Zahl

Das ist das Einzige, was du dir merken musst.

Eine Faithfulness von 0,87 sagt für sich genommen fast nichts. Es gibt keine
Schwelle, ab der eine Antwort gut ist. Beide Zahlen stammen von einem Sprachmodell,
das ein anderes Sprachmodell bewertet, also tragen sie dessen Meinungen und dessen
Rauschen mit.

Was dir etwas sagt:

```
Faithfulness 0,87  ->  0,71   nach dem Wechsel auf Chunking `heading`
```

Das ist ein Signal. Vergleiche Durchläufe miteinander, und sei misstrauisch bei
jedem Schluss, der auf einer einzelnen Zahl beruht.

## Einschalten

Zwei Dinge müssen zutreffen.

**1. Den Evaluations-Dienst starten.** Er läuft als eigener Container und gehört
nicht zum normalen Start:

```bash
docker compose --profile eval up -d
```

**2. In der Konfiguration einschalten.** In deiner `rag.config.yaml`:

```yaml
evaluation:
  enabled: true
  metrics: [faithfulness, relevance]
  judge_model: null      # null nimmt models.chat_model
  show_inline: true
```

Starte die App neu und stelle eine Frage. Die Werte erscheinen unter der Antwort:

```
Faithfulness: 92% · Relevance: 87%
```

Wenn du die Zahlen nur für das Dashboard sammeln willst, ohne dass sie unter jeder
Antwort auftauchen, setze `show_inline: false`.

Läuft der Dienst nicht, speichert die App einfach nichts. Du siehst keinen Fehler,
und die Antworten sind unverändert.

## Was es kostet

Jede bewertete Antwort kostet **zwei Bewertungs-Aufrufe und einen
Embedding-Aufruf**, zusätzlich zur Antwort selbst. Das ist der ganze Grund, warum
das Ganze optional ist.

Schnell ist es außerdem nicht. Gemessen gegen ein selbst betriebenes 70B-Modell,
das normale Anfragen in etwa einer Sekunde beantwortete, dauerte die Bewertung einer
Antwort **rund 40 Sekunden**. Die beiden Kennzahlen laufen gleichzeitig, es ist also
die langsamere der beiden und nicht ihre Summe, aber immer noch weit mehr als die
Antwort selbst gebraucht hat.

Warten musst du deswegen nicht. Die Bewertung startet erst, wenn die Antwort auf dem
Bildschirm steht und gespeichert ist, du kannst also direkt weiterfragen. Die Werte
erscheinen unter der Antwort, sobald sie fertig sind, unter Umständen eine halbe
Minute später.

Wenn die Bewertung viele Minuten statt einiger Sekunden braucht, denkt das
Bewertungsmodell wahrscheinlich nicht, sondern scheitert. Prüfe, ob das angegebene
Modell tatsächlich antwortet.

Die Bewertungs-Aufrufe gehen über dasselbe Gateway und dieselben Zugangsdaten wie
alles andere, du musst also nichts zusätzlich einrichten.

Richte `judge_model` möglichst auf ein **anderes** Modell als das bewertete. Ein
Modell, das seine eigene Arbeit benotet, ist mit sich selbst meist großzügig.

## Das Dashboard

Wenn der Dienst läuft, liegt das Dashboard auf **<http://localhost:8001>**.

Es gibt dafür einen Link in der Kopfzeile der App, der ist aber standardmäßig
auskommentiert, damit Leute, die die Evaluation nie einschalten, keinen Link ins
Leere bekommen. Zum Aktivieren den `[[UI.header_links]]`-Block für Evaluation in
`apps/chainlit/.chainlit/config.toml` einkommentieren.

Die Tabelle zeigt eine Zeile pro Konfiguration:

| Spalte | Bedeutung |
|---|---|
| Configuration | Chat-Modell, Embedding-Modell, Chunking und Collection |
| Answers | Wie viele Antworten damit bewertet wurden |
| Faithfulness | Mittelwert über diese Antworten |
| Relevance | Mittelwert über diese Antworten |
| Thumbs | Wie oft „hilfreich" bzw. „nicht hilfreich" geklickt wurde |

Achte auf die Anzahl der Antworten. „0,91 über 3 Antworten" und „0,91 über 300"
sind nicht dieselbe Aussage.

Eine Antwort, bei der der Bewertungs-Aufruf fehlgeschlagen ist, zählt weiter bei
`Answers`, liefert aber keinen Wert. Ein Fehler der Bewertung ist kein Beweis für
eine schlechte Antwort, deshalb bleibt sie aus dem Mittelwert heraus statt als Null
zu zählen.

## Zwei Konfigurationen vergleichen

Die Werte werden nach Chat-Modell, Embedding-Modell, Chunking-Strategie,
Chunk-Größe **und Collection** gruppiert. Zwei Konfigurationen, die sich in einem
dieser Punkte unterscheiden, erscheinen als getrennte Zeilen.

Um sie sauber zu vergleichen, gib jeder ihre eigene Collection und importiere in
beide. Wenn du zwei Chunking-Strategien auf dieselbe Collection richtest,
überschreibt der zweite Import den ersten, und ältere Zeilen im Dashboard
beschreiben dann einen Korpus, der so nicht mehr existiert.

Ein praktischer Durchlauf:

1. Mit Strategie A in die Collection `papers_a` importieren.
2. Deine zehn üblichen Fragen stellen.
3. Mit Strategie B in die Collection `papers_b` importieren.
4. Dieselben zehn Fragen stellen.
5. Die beiden Zeilen vergleichen.

Dass es *dieselben* Fragen sind, ist wichtig. Andere Fragen ergeben andere Werte,
ganz unabhängig von der Konfiguration.

## Wenn jemand „nicht hilfreich" klickt

Chainlit bietet bei den Daumen-Buttons schon ein Kommentarfeld an. Wird zu einem
Daumen nach unten ein Kommentar hinterlassen, wird er einer von vier Kategorien
zugeordnet, die gleich ein Hinweis darauf sind, wo du nachsehen solltest:

| Kategorie | Bedeutung | Wo nachsehen |
|---|---|---|
| `hallucination` | Die Antwort behauptet etwas, das die Dokumente nicht hergeben | System-Prompt und die Faithfulness-Werte |
| `wrong_document` | Es wurde die falsche Quelle abgerufen oder zitiert | Chunking-Strategie und Embedding-Modell |
| `incomplete` | Richtig, aber Wesentliches fehlt | `retrieval.top_k` und Chunk-Größe |
| `irrelevant` | Die Antwort geht nicht auf die Frage ein | Wie Fragen beim Retrieval ankommen |

Der ursprüngliche Kommentar wird immer aufbewahrt, du kannst also nachlesen, was
jemand tatsächlich geschrieben hat. Ist die Zuordnung unklar, wird der Kommentar
ohne Kategorie gespeichert, statt in die nächstbeste gedrückt zu werden.

Ein Daumen nach oben wird nie klassifiziert. Er ist kein Fehler, und ihn durch eine
Fehlerliste zu schicken würde einen erfinden.

## Was dir das nicht sagt

- **Ob eine Antwort nützlich ist.** Eine belegte, relevante Antwort kann trotzdem
  nicht weiterhelfen. Nichts hiervon ersetzt es, selbst ein paar Antworten zu
  lesen.
- **Ob das Retrieval etwas übersehen hat.** Beide Kennzahlen sehen nur die
  Textstellen an, die tatsächlich abgerufen wurden. Wurde die richtige Stelle nie
  gefunden, sinkt keine der beiden Zahlen. Das zu messen bräuchte pro Frage eine
  von Hand geschriebene richtige Antwort, und die verlangt diese Funktion nicht von
  dir.
- **Irgendetwas Absolutes.** Das sei wiederholt, weil die Zahlen dazu einladen.

## Wo das läuft

Die Bewertung passiert in einem eigenen Dienst, nicht in der App. Die App schickt
Frage, Antwort und die abgerufenen Textstellen erst, wenn die Antwort schon auf dem
Bildschirm steht, und ergänzt danach die Werte. Eine langsame Bewertung verzögert
also nie eine Antwort, und mit ausgeschalteter Evaluation verhält sich die App
genau so, als gäbe es all das nicht.

Die Werte liegen in einer eigenen SQLite-Datenbank im Volume `eval_db`, getrennt
von deinem Chat-Verlauf.
