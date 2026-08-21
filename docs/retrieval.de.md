# Hybride Suche

Semantische Suche ist gut in Bedeutung und schlecht in exakten Zeichenketten. Frag
sie im mitgelieferten Paper-Korpus nach `ab15898`, der Katalognummer eines Antikörpers,
und sie liefert Textstellen aus einem anderen Paper — für ein Embedding-Modell ist eine
Katalognummer kaum Signal, und thematisch ähnliche Absätze gibt es in jedem der Paper.
Bei einem Korpus aus Katalognummern, Chemikalien, Zelllinien und Eigennamen ist das der
Unterschied zwischen einer richtigen Antwort und einer selbstbewusst falschen.

Die hybride Suche stellt der semantischen eine zweite, rein lexikalische Suche zur
Seite und führt beide Ranglisten zusammen. Sonst ändert sich nichts: Der Assistent
bekommt weiterhin `top_k` Textstellen, und Qdrant führt zusammen, bevor irgendetwas
beim Modell ankommt.

## Was das behebt, gemessen

Neun Paper, einmal indexiert. Gleicher Korpus, gleiches Embedding-Modell, gleiche
Fragen — nur der Anfrageweg unterscheidet sich:

| Art der Anfrage | Dense | Hybrid |
|---|---|---|
| natürliche Frage mit einem seltenen Begriff | 76% | **93%** |
| der Begriff allein | 50% | **90%** |

Top-1-Trefferquote auf das richtige Dokument, über 30 Bezeichner, die je in genau
einem Paper eines Neun-Paper-Korpus vorkommen — Katalognummern, Zelllinien,
Fluorophore, Chemikalien. Genau das, was ein Embedding auf „irgendwas mit
Labormethoden" abbildet, während die exakte Zeichenkette das einzige Signal ist.

Der Fehler von Dense ist dabei kein knappes Danebenliegen. Auf `ab15898` steht
Lin 2024 auf Platz 1, und der Chunk mit der Nummer ist überhaupt nicht unter den
ersten fünf; mit Hybrid steht Schmidt 2022 vorn, das Paper, in dem die Nummer
tatsächlich steht. Bei `carbonylcyanide-m-chlorophenylhydrazone` dasselbe Muster:
Dense landet auf Schmidt 2022, Hybrid auf Schauer 2018. Ein Assistent, der den ersten
Treffer liest, antwortet also aus dem falschen Paper — und tut das selbstbewusst,
weil die Textstelle thematisch durchaus passt.

## Einschalten

```yaml
retrieval:
  hybrid: true
  fusion: rrf        # oder dbsf
  prefetch_limit: 30 # Kandidaten pro Zweig vor dem Zusammenführen
```

Die mitgelieferte Instanz `examples/papers` bringt diese Werte schon mit: ein frischer
Clone sucht hybrid und kann per Neustart gegen `hybrid: false` vergleichen. Die
Schema-Vorgabe bleibt aus, eine bestehende Instanz verhält sich also unverändert, bis
du sie einschaltest.

Der Ingest schreibt den lexikalischen Vektor in jede Collection, die er **neu
anlegt** — er ist eine lokal berechnete Wortzählung und kostet nichts. Für diese ist
`hybrid` damit ein reiner Anfrage-Schalter: umlegen, neu starten, vergleichen,
zurücklegen. Kein erneuter Ingest, und eine einzige Collection taugt für ein
Dense-gegen-Hybrid-A/B. Eine Collection, die vor diesem Feature entstanden ist, ist
rein dense und bleibt es, bis der nächste Ingest sie neu aufbaut (siehe unten) — bis
dahin schreibt ein Ingest in sie weiterhin nur dense Vektoren, statt zu scheitern.

!!! warning "Collections von vor diesem Feature bauen sich einmalig selbst neu"
    Eine Collection, die vor den lexikalischen Vektoren angelegt wurde, ist rein
    dense — ihre Punkte können nachträglich keinen bekommen. Der Ingest erkennt
    das und baut sie neu, ohne dass du ein Flag kennen musst:

    ```bash
    docker compose run --rm ingest python -m kb.ingest
    ```

    Er nennt den Grund und die Zahl der verworfenen Punkte, denn ein Neuaufbau
    embeddet den gesamten Korpus neu, und das ist abgerechneter Gateway-Verkehr.
    Er greift nur bei einem echten Defekt: Mit `hybrid: false` ist eine rein
    dense Collection völlig brauchbar und bleibt unangetastet.

    **Bis du das ausführst, startet die App nicht**, statt rein dense zu laufen,
    während die Config etwas anderes behauptet — sie kann nicht indexieren, also
    kann sie das nicht selbst beheben. `make check` und der App-Start nennen
    denselben einen Befehl. Die Alternative wäre eine stille Herabstufung: Hybrid
    sieht aktiv aus und trägt einfach nie etwas bei.

    Dieselbe Verweigerung greift bei einer **Änderung des lexikalischen Formats**:
    Der Tokenizer entscheidet, welche Begriffe gespeichert sind, also macht ein
    Update, das ihn ändert, die bestehenden Begriffe unauffindbar. Die
    Formatversion wird beim Ingest pro Collection festgehalten und bei jedem Lauf
    verglichen — genau wie das Embedding-Modell.

## Die drei Einstellungen

**`hybrid`** — standardmäßig aus. Wirkt nur auf die Anfrage: zwei Suchen statt einer,
dann das Zusammenführen. Die Daten darunter sind in beiden Fällen dieselben.

**`fusion`** — wie aus zwei Ranglisten eine wird.

- `rrf` (Reciprocal Rank Fusion) führt über die *Position* zusammen: Platz 3 zählt
  gleich viel, unabhängig vom zugehörigen Score. Robust, und die richtige Vorgabe,
  solange unklar ist, welche Hälfte die Suche trägt.
- `dbsf` (Distribution-Based Score Fusion) führt über die *Scores* zusammen, je Liste
  normalisiert. Kann RRF schlagen, wenn ein Verfahren deutlich stärker ist, und kann
  unruhiger sein, wenn die Scores schlecht streuen. Beides ausprobieren und messen —
  nicht aus Prinzip umstellen.

**`prefetch_limit`** — wie viele Kandidaten jede Suche vor dem Zusammenführen
beisteuert. Der Wert muss mindestens `max_top_k` betragen — das erzwingt der
Config-Loader, sobald `hybrid` an ist, denn ein kleinerer Pool, dessen beide Zweige
dieselben Kandidaten liefern, kann auf weniger als `top_k` Ergebnisse
zusammenschmelzen. 30 auf 5 ist ein vernünftiger Anfang; ein höherer Wert kostet
etwas Anfragezeit und sonst nichts.

!!! warning "`score_threshold` begrenzt nur die semantische Hälfte"
    Ein lexikalischer Treffer hat keinen vergleichbaren Ähnlichkeitswert, deshalb
    gilt `retrieval.score_threshold` für die semantische Suche und nicht für die
    lexikalische. Mit `hybrid: true` kann eine Textstelle also allein über einen
    gemeinsamen Begriff beim Modell landen, obwohl ihre Ähnlichkeit weit unter der
    Schwelle liegt. Wenn die Schwelle das ist, was themenfremde Fragen unbeantwortet
    lässt, prüfe nach dem Einschalten, ob das noch zutrifft.

    Aus demselben Grund sucht `verify_claim` bewusst rein semantisch: Es ist die
    einzige Stelle, die einen Wert gegen eine feste Schwelle prüft, und ein
    zusammengeführter Wert ist ein Rang, keine Ähnlichkeit.

## Was das kostet

Nichts, in der Hinsicht, auf die es ankommt: **kein Modell, keine GPU, keine neue
Abhängigkeit.** Der lexikalische Vektor ist eine Wortzählung. Die IDF-Gewichtung
übernimmt Qdrant serverseitig, die Anwendung berechnet und speichert also keine
Korpusstatistik. Der Ingest wird geringfügig langsamer, Anfragen machen einen zweiten
Zugriff innerhalb der Datenbank.

## Wann sich ein Reranker lohnt

Ein Reranker ist ein Modell, das jede Kandidatenstelle zusammen mit der Frage erneut
liest und neu bewertet. Er ist tatsächlich besser als Fusion — und tatsächlich teuer:
Am AI-Gateway steht kein Reranker bereit, er liefe also lokal auf der CPU. Rechne mit
einem Download im Gigabyte-Bereich und mehreren Sekunden pro Anfrage.

Entscheide das nicht über die Korpusgröße. Entscheide es über eine Zahl, die du messen
kannst:

!!! tip "Der Test: recall@30 gegen recall@5"
    - **recall@30 deutlich höher als recall@5** → die richtige Stelle *wird* gefunden,
      nur zu weit hinten einsortiert. Genau diese Lücke schließt ein Reranker, und
      genau darauf lohnt es sich zu warten.
    - **recall@30 ≈ recall@5** → die richtige Stelle wird überhaupt nicht gefunden.
      Ein Reranker kann nicht nach vorn holen, was nie abgerufen wurde. Dann liegt es
      am Chunking, am Embedding-Modell oder am lexikalischen Tokenizer.

Die Korpusgröße korreliert mit dieser Lücke — Zehntausende Chunks, viele fast
identische Dokumente oder ein Thema, das einen heterogenen Korpus dominiert, treiben
recall@30 und recall@5 auseinander. Aber die Größe ist das Symptom; die Lücke ist das
Signal, und sie ist das, was du tatsächlich prüfen kannst.

Zwei günstigere Dinge vorher:

1. **Quellenvielfalt.** Wenn deine fünf besten Stellen fünf Chunks desselben Dokuments
   sind, hast du kein Ranking-Problem, sondern ein Streuungsproblem.
2. **Payload-Boosts.** Qdrant kann Ergebnisse per Arithmetik über Payload-Felder neu
   bewerten — Tabellen bei einer Tabellenfrage hochgewichten, nach Abschnittsabstand
   abfallen lassen. Kein Modell, keine Inferenz.

Wenn es doch ein Reranker sein soll: mit `bge-reranker-base` (~278M Parameter)
anfangen, nicht mit `bge-reranker-v2-m3` (~2,3 GB). Auf der CPU ist das der
Unterschied zwischen benutzbar und nicht.

## Wie es funktioniert

Beim Ingest bekommt jeder Chunk einen zweiten Vektor: seine Begriffe, gezählt.
Begriffe werden kleingeschrieben, und Bindestrich-Komposita bleiben ganz —
`carbonylcyanide-m-chlorophenylhydrazone` ist ein Begriff und nicht drei Bruchstücke,
von denen eines ein einzelnes `m` ist. Dieses Detail macht den Großteil des Gewinns
aus und steht in `apps/chainlit/kb/sparse.py`, falls dein Korpus anders tokenisiert
werden muss.

Bei der Anfrage wird die Frage genauso tokenisiert — **ohne Funktionswörter**.
Gespeicherte Chunks behalten jedes Wort; gefiltert wird nur die Frage.

Diese Ausnahme ist nicht kosmetisch, und IDF macht sie nicht entbehrlich. Qdrants
`Modifier.IDF` wendet den IDF-Term von BM25 an, nicht die beiden anderen — keine
TF-Sättigung, keine Längennormalisierung. Ein Begriff trägt also `tf × idf` bei,
linear und in `tf` unbegrenzt. Im Beispielkorpus gewann „Was ist X und wofür wurde es
verwendet?" ein Chunk, der X überhaupt nicht enthält: zwölfmal `und` ergibt
12 × 1,67 = 20,09, gegen 1 × 5,47 für das seltene Kompositum, das das richtige
Dokument ausmacht. IDF hat die Begriffe richtig gewichtet und trotzdem verloren, denn
es begrenzt das Gewicht eines Begriffs und nicht, wie oft ein Begriff zählen darf.
Ohne die Filterung lag Hybrid *unter* der rein semantischen Suche: 36% gegen 76%.

Beide Suchen laufen dann mit je `prefetch_limit` Treffern, und Qdrant führt sie auf
`top_k` zusammen. Mit
`hybrid: false` bleibt die Anfrage die gewohnte einzelne Dense-Suche — der
lexikalische Vektor liegt dann ungenutzt bereit, bis der Schalter umgelegt wird.
