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
(Eine dritte Kennzahl, **Ähnlichkeit**, vergleicht sehr wohl gegen eine gespeicherte
Referenzantwort — sie taucht nur in Benchmark-Wiederholungen auf, siehe
[Gold-Datensatz & Benchmarks](#gold-datensatz-benchmarks).)

### Wie sie berechnet werden

Das Abzeichen zeigt dasselbe, wenn du es anklickst, du sollst diese Seite also
nicht offen halten müssen. Hier steht es zum Nachschlagen.

**Treue** zerlegt die Antwort in einzelne Aussagen und prüft jede gegen die
abgerufenen Textstellen:

```text
Treue = gedeckte Aussagen / alle Aussagen
```

Deshalb ist die Zahl mehr als eine Note: 0,5 heißt nicht „mittelmäßig", sondern
*die Hälfte der Aussagen dieser Antwort war nicht durch die Quellen gedeckt*. Das
Abzeichen-Fenster listet sie einzeln auf, mit der Begründung des Bewertungsmodells, du
siehst also welche Aussage durchgefallen ist und musst nicht raten.

**Relevanz** erzeugt aus der Antwort Fragen und vergleicht sie mit der Frage, die
tatsächlich gestellt wurde:

```text
Relevanz = ⌀ cos( E(erzeugte Frageᵢ) , E(echte Frage) )
```

`E(...)` ist dein Embedding-Modell, diese Kennzahl kostet also zusätzlich zum
Bewertungs-Aufruf einen Embedding-Aufruf.

**Der Wert im Abzeichen** ist der laufende Mittelwert über das Gespräch:

```text
⌀ = (1/n) · Σ Wertᵢ
```

### Relevanz 0% heißt meistens: der Assistent hat sich enthalten

Das solltest du wissen, bevor es dich erschreckt. Wird die Antwort als
*enthaltend* eingeschätzt — „steht nicht in den Dokumenten" — wird die Ähnlichkeit
verworfen und die Relevanz auf **0** gesetzt:

```text
Relevanz = ⌀(Cosinus) × (0 wenn die Antwort sich enthält, sonst 1)
```

0% heißt also nicht, dass die Antwort am Thema vorbeiging. Meistens heißt es, dass
der Assistent die Antwort verweigert hat, und genau das *soll* er tun, wenn der
Korpus eine Frage nicht abdeckt. Das Fenster sagt es in diesem Fall
ausdrücklich.

## Lies die Veränderung, nicht die Zahl

Das ist das Einzige, was du dir merken musst.

Eine Faithfulness von 0,87 sagt für sich genommen fast nichts. Es gibt keine
Schwelle, ab der eine Antwort gut ist. Beide Zahlen stammen von einem Sprachmodell,
das ein anderes Sprachmodell bewertet, also tragen sie dessen Meinungen und dessen
Rauschen mit.

Was dir etwas sagt:

```text
Faithfulness 0,87  ->  0,71   nach dem Wechsel auf Chunking `heading`
```

Das ist ein Signal. Vergleiche Durchläufe miteinander, und sei misstrauisch bei
jedem Schluss, der auf einer einzelnen Zahl beruht.

## Einschalten

**In der Konfiguration einschalten.** Der Evaluations-Dienst startet mit dem
übrigen Stack; das hier ist der einzige Schalter. In deiner `rag.config.yaml`:

```yaml
evaluation:
  enabled: true
  metrics: [faithfulness, relevance]
  judge_model: null      # null nimmt models.chat_model
  show_badge: true
```

Starte die App neu und stelle eine Frage. Über dem Eingabefeld erscheint ein
kleines Abzeichen:

```text
Treue 67% ↗ · Relevanz 88% · 3 Antworten
```

Das ist das Gespräch bis hierher, nicht die letzte Antwort: ein laufender
Mittelwert über alle bewerteten Antworten dieses Chats, mit der Anzahl daneben.

**Auf die Anzahl kommt es an.** „67% über 1 Antwort" und „67% über 20" sind nicht
dieselbe Aussage, deshalb steht sie immer dabei.

**Jede Kennzahl hat ihren eigenen Pfeil und vergleicht die letzte Antwort mit dem
Mittelwert dieser Kennzahl.** ↗ heißt, die letzte Antwort war besser als der Schnitt
des Gesprächs, ↘ schlechter, und kein Pfeil heißt, sie lag etwa dort, wo der
Mittelwert schon war. Die beiden bewegen sich unabhängig voneinander —
`Treue 58% ↘ · Relevanz 17% ↗` ist eine normale und durchaus nützliche Anzeige.
Sinnvoll ist ein Pfeil erst ab zwei Antworten, vorher erscheint er nicht.

**Klicke auf das Abzeichen** für die ganze Erklärung: welche Aussagen die letzte
Antwort gemacht hat, welche davon die Quellen decken, und den Rechenweg für jede
Zahl. Es bleibt offen, du kannst also durch eine lange Liste scrollen, und schließt
bei einem zweiten Klick, mit Escape oder per Klick daneben. Du sollst das nicht im
Kopf haben oder dafür auf diese Seite zurückkommen müssen.

Das Abzeichen gehört zu einem Gespräch und erscheint deshalb erst, wenn darin eine
Antwort bewertet wurde. Auf der Startseite steht nichts, statt der Zahlen von dem,
was du zuletzt gemacht hast.

Abzeichen und Panel erscheinen auf Deutsch oder Englisch und folgen demselben Signal
wie der Rest der Oberfläche, siehe
[Sprache der Oberfläche](configuration.md#sprache-der-oberflache). Ausnahme sind die
Begründungen des Judges je Aussage: die stammen aus den englischen Prompts von RAGAS
und bleiben so oder so englisch.

Wenn du die Zahlen sammeln willst, ohne sie jemandem vor die Nase zu setzen, setze
`show_badge: false`. Die Daten sammeln sich trotzdem für spätere Vergleiche.

Läuft der Dienst nicht, speichert die App einfach nichts. Du siehst keinen Fehler,
und die Antworten sind unverändert.

## Was es kostet

Jede bewertete Antwort kostet **drei Bewertungs-Aufrufe und zwei
Embedding-Aufrufe**, zusätzlich zur Antwort selbst: zwei, um die Antwort in Aussagen
zu zerlegen und zu prüfen, einen, um daraus Fragen zu erzeugen, und die Embeddings
für den Vergleich dieser Fragen mit deiner. Das ist der ganze Grund, warum das Ganze
optional ist.

Schnell ist es außerdem nicht: Die Bewertung einer Antwort dauert **rund 15
Sekunden**. Die beiden Kennzahlen laufen gleichzeitig, es ist also die langsamere der
beiden und nicht ihre Summe, und die langsamere ist immer die Treue.

Ein größeres Bewertungsmodell macht es nicht langsamer und ein kleineres nicht
schneller. Gemessen über `ministral-3-14b`, `gemma-4-31b` und `llama-3-3-70b` liegen
alle drei innerhalb einer Sekunde beieinander. Was die Zeit bestimmt, ist wie viel die
Antwort behauptet, denn jede Aussage braucht eine ausgeschriebene Begründung.

### Warum es bezahlbar bleibt

Die Treue prüft jede Aussage in einer eigenen Anfrage, alle gleichzeitig, statt alle
Aussagen in eine zu packen. Und jede Aussage wird nur gegen die paar Textstellen
geprüft, die am ehesten dazu passen, ausgewählt über Wortüberlappung, statt gegen den
ganzen abgerufenen Kontext.

Beides zählt, das Zweite besonders, wenn eine Antwort mit `fetch_document` entstanden
ist und damit ein ganzes Paper abrufen kann. Gemessen an einer echten Antwort mit 63
Textstellen, 71 kB Kontext und 12 Aussagen:

| | Zeit | Input-Tokens |
|---|---|---|
| alle Aussagen in einer Anfrage | 39,3 s | 19.244 |
| eine pro Aussage, jeweils ganzer Kontext | 75,4 s | 226.594 |
| **eine pro Aussage, geroutet** | **12,8 s** | 40.181 |

Aufteilen ohne Routing ist schlechter als gar nicht aufteilen. Mit Routing ist es
dreimal schneller als eine gebündelte Anfrage, bei etwa doppelt so vielen
Input-Tokens. Bei einem selbst betriebenen Gateway ist das Rechenzeit, die du schon
besitzt, und genau davon geht dieses Template aus; bei einer bezahlten API sind es
etwa doppelte Kosten pro bewerteter Antwort.

Die Werte ändern sich dadurch nicht. Es ist RAGAS' eigener Prompt mit einer kürzeren
Liste, und an einer absichtlich gemischten Antwort — drei echte und drei erfundene
Aussagen — lieferten gebündelte und geroutete Prüfung dieselben Urteile und markierten
genau dieselben drei als nicht gedeckt.

Warten musst du deswegen nicht. Die Bewertung startet erst, wenn die Antwort auf dem
Bildschirm steht und gespeichert ist, du kannst also direkt weiterfragen. Das
Abzeichen aktualisiert sich von selbst, sobald ein Wert fertig ist, unter Umständen
eine halbe Minute nach der Antwort.

Weil das Abzeichen zum Gespräch gehört und nicht zu einer einzelnen Nachricht, zählt
ein Wert auch dann noch, wenn er erst fertig wird, nachdem du schon weitergefragt
hast, und er ist nach einem Neuladen der Seite weiterhin da.

Wenn die Bewertung viele Minuten statt einiger Sekunden braucht, denkt das
Bewertungsmodell wahrscheinlich nicht, sondern scheitert. Prüfe, ob das angegebene
Modell tatsächlich antwortet.

Solange eine Bewertung läuft, zeigt das Abzeichen `Bewertung läuft…`. Eine fehlende
Zahl heißt also: entweder hat sie nicht angefangen oder sie ist gescheitert, nie dass
du weiter warten solltest.

Die Bewertungs-Aufrufe gehen über dasselbe Gateway und dieselben Zugangsdaten wie
alles andere, du musst also nichts zusätzlich einrichten.

Richte `judge_model` möglichst auf ein **anderes** Modell als das bewertete. Ein
Modell, das seine eigene Arbeit benotet, ist mit sich selbst meist großzügig — und
mit `judge_model: null` *folgt der Judge dem Modell, das geantwortet hat*: jedes
Modell benotet dann seine eigenen Antworten, und die Werte sind über Modelle hinweg
nicht mehr vergleichbar. Das mitgelieferte `papers`-Beispiel nagelt deshalb einen
kleinen, verifizierten Judge fest. Bevor du einem Judge traust, prüfe ihn so, wie
dieses Projekt es tut: gib ihm eine Antwort mit drei gedeckten und drei erfundenen
Aussagen und schau, ob er genau die drei erfundenen markiert. Der verwendete Judge
wird bei jeder Score-Zeile mitgespeichert — ein späterer Wechsel macht alte Zahlen
also nie mehrdeutig.

## Der Vergleich — der zweite Tab des Abzeichens

Klick auf das Abzeichen öffnet das Panel mit zwei Tabs. **Dieses Gespräch** ist
die oben beschriebene Gesprächsansicht. **Vergleich** ist die Gegenüberstellung:
eine Zeile je Konfiguration, geholt über die App selbst — der Browser spricht nie
direkt mit dem Eval-Dienst, also gibt es keine zweite Adresse, keinen zweiten
Login und keine zweite Sprache.

Die Tabelle zeigt eine Zeile pro Konfiguration:

| Spalte | Bedeutung |
|---|---|
| Konfiguration | Chat-Modell, darunter Chunking und Collection |
| n | Wie viele Antworten damit bewertet wurden |
| Treue | Mittelwert über diese Antworten |
| Relevanz | Mittelwert über diese Antworten |

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

Das Chat-Modell ist das, das **tatsächlich geantwortet hat**, nicht das aus der
YAML: Wechselst du im ⚙️-Panel das Modell, wandern die Werte in eine neue Zeile.
Ebenso beim Chunking — ein `data_sources[].chunking` schlägt den globalen Block,
und genau das macht das mitgelieferte `papers`-Beispiel. Widersprechen sich mehrere
Quellen, steht dort `semantic+heading`: mehrere Quellen können eine Collection
füllen, und dann gibt es keine einzelne richtige Antwort.

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

## Gold-Datensatz & Benchmarks

Live-Werte sagen dir, wie jede Konfiguration mit dem umging, *was die Leute zufällig
gefragt haben*. Ein Benchmark stellt jeder Konfiguration *dieselben Fragen* — und
die kommen von dir.

**Gold markieren — der Quest-Marker.** Wenn eine Antwort über den Schwellen
liegt (`gold_min_faithfulness`, Standard 0,9, und `gold_min_relevance`, Standard
0,8), erscheint am Abzeichen ein gelbes **!** — die Videospiel-Konvention für
„hier gibt es etwas zu tun". Ein Klick aufs Abzeichen öffnet das Panel mit dem
Angebot: *Starke Antwort — als Gold-Referenz speichern?* Bei einem Gespräch mit
mehreren Runden stellt Speichern eine Frage: das **ganze Gespräch** oder **nur die
letzte Frage & Antwort**. Mehr Auswahl gibt es absichtlich nicht — ein
Gold-Gespräch wird in Reihenfolge wiederholt, und einzeln herausgepickte Runden
ließen spätere Fragen auf Kontext verweisen, den das wiederholende Modell nie
gesehen hat. Nimm das einzelne Paar, wenn die Frage für sich steht; das Gespräch,
wenn der Verlauf der Punkt ist. Ignorieren (✕) zieht den Marker für diese Antwort
zurück. Der Judge späht
vor, du entscheidest: Hohe Werte heißen nicht *vollständig*, deshalb wird nie
automatisch gespeichert. Eine der Schwellen auf `null` setzen schaltet den
Vorschlag ganz ab.

**Benchmark starten** stellt den gesamten Gold-Datensatz mit einem gewählten
Chat-Modell erneut: Runde für Runde, mit den *eigenen* vorherigen Antworten des
wiederholenden Modells als Gesprächsverlauf — eine falsche Antwort in Runde 1
verändert also die Prämisse von Runde 2, genau wie bei einem echten Nutzer. Diese
Drift ist das, was ein Gesprächs-Benchmark misst. Jede Runde wird auf Treue,
Relevanz und **Ähnlichkeit** bewertet — Embedding-Kosinus gegen die Gold-Antwort
dieser Runde. Gestartet wird ein Lauf vorerst über die Kommandozeile (unten); ein
Auslöser im Abzeichen-Panel ist der geplante nächste Schritt.

Drei Regeln halten die Zahlen ehrlich:

- **Der Judge ist je Lauf fest** — `evaluation.judge_model`, sonst das konfigurierte
  Chat-Modell — und folgt nie dem wiederholten Modell. Ein Modell, das sich selbst
  benotet, ist kein Vergleich.
- **Replay-Zeilen landen nie im Live-Vergleich.** Wiederholte Antworten
  überspringen die Zitat-Link- und Abbildungs-Nachbearbeitung der App — vergleiche
  also Läufe mit Läufen, nicht mit Live-Zeilen.
- **Die Abdeckung steht als `n/alle Runden` dabei.** Macht ein neu ingestierter
  Korpus Gold-Fragen unbeantwortbar, zeigt sich die Lücke, statt sich zu verstecken.

Es gibt auch eine Kommandozeile, praktisch für mehrere Modelle in einem Rutsch:

```bash
docker compose exec chainlit python benchmark.py --models gemma-4-31b gpt-oss-120b
```

`--judge MODELL` setzt einen anderen Judge; `--label NAME` benennt den Lauf. Wer
zwei *Läufe* vergleicht, braucht auch denselben Judge — das Label trägt einen
Zeitstempel, damit Läufe unterscheidbar bleiben.

**Ein Gold-Gespräch stilllegen** hat noch keine Oberfläche; es ist eine Zeile gegen
die Eval-Datenbank (die Zeile bleibt erhalten, sie verlässt nur den aktiven Satz):

```bash
docker compose exec eval python -c "import sqlite3; c=sqlite3.connect('/app/.evaldb/eval.sqlite3'); c.execute(\"UPDATE gold_answers SET active=0 WHERE id='<gold-id>'\"); c.commit()"
```

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
