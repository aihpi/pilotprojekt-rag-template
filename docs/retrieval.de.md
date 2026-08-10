# Hybride Suche

Semantische Suche ist gut in Bedeutung und schlecht in exakten Zeichenketten. Frag
sie nach `BSI-Standard 200-2`, und sie liefert bereitwillig `200-1` — für ein
Embedding-Modell bedeuten diese beiden Sätze fast dasselbe. Bei einem Korpus aus
Normnummern, Fachkomposita und Eigennamen ist das der Unterschied zwischen einer
richtigen Antwort und einer selbstbewusst falschen.

Die hybride Suche stellt der semantischen eine zweite, rein lexikalische Suche zur
Seite und führt beide Ranglisten zusammen. Sonst ändert sich nichts: Der Assistent
bekommt weiterhin `top_k` Textstellen, und Qdrant führt zusammen, bevor irgendetwas
beim Modell ankommt.

## Was das behebt, gemessen

Acht kurze deutsche Sätze zu den BSI-Standards, zweimal indexiert — einmal rein
dense, einmal hybrid. Gleiche Anfrage, gleicher Korpus, gleiches Embedding-Modell:

| Anfrage | Dense, Platz 1 | Hybrid, Platz 1 |
|---|---|---|
| `BSI-Standard 200-2` | ❌ 200-1 (0,5924) — 200-2 auf Platz 2 mit 0,5902 | ✅ 200-2 (0,8333) |
| `200-3` | ❌ 200-2 (0,4058) | ✅ 200-3 (0,8333) |

Dense lag beide Male auf Platz 1 daneben, im ersten Fall mit einem Abstand von
0,0022 — reiner Zufall. Das richtige Dokument war in den Ergebnissen, nur nicht
vorn, und ein Assistent, der den ersten Treffer liest, antwortet aus dem falschen
Standard.

## Einschalten

```yaml
retrieval:
  hybrid: true
  fusion: rrf        # oder dbsf
  prefetch_limit: 30 # Kandidaten pro Zweig vor dem Zusammenführen
```

Der Ingest schreibt den lexikalischen Vektor **immer** — er ist eine lokal
berechnete Wortzählung und kostet nichts. `hybrid` ist damit ein reiner
Anfrage-Schalter: umlegen, neu starten, vergleichen, zurücklegen. Kein erneuter
Ingest, und eine einzige Collection taugt für ein Dense-gegen-Hybrid-A/B.

!!! warning "Collections von vor diesem Feature brauchen einmalig einen Neuaufbau"
    Eine Collection, die vor den lexikalischen Vektoren angelegt wurde, ist rein
    dense — ihre Punkte können nachträglich keinen bekommen. Mit `hybrid: false`
    funktioniert sie unverändert; für Hybrid einmal neu indexieren:

    ```bash
    docker compose run --rm ingest python -m kb.ingest --recreate
    ```

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
Config-Loader, denn ein kleinerer Pool, dessen beide Zweige dieselben Kandidaten
liefern, kann auf weniger als `top_k` Ergebnisse zusammenschmelzen. 30 auf 5 ist
ein vernünftiger Anfang; ein höherer Wert kostet etwas Anfragezeit und sonst nichts.

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
Begriffe werden kleingeschrieben, und Bindestrich-Komposita bleiben ganz — `BSI-Standard`
ist ein Begriff und nicht zwei Allerweltswörter. Dieses Detail macht den Großteil des
Gewinns aus und steht in `apps/chainlit/kb/sparse.py`, falls dein Korpus anders
tokenisiert werden muss.

Bei der Anfrage wird die Frage genauso tokenisiert, beide Suchen laufen mit je
`prefetch_limit` Treffern, und Qdrant führt sie auf `top_k` zusammen. Mit
`hybrid: false` bleibt die Anfrage die gewohnte einzelne Dense-Suche — der
lexikalische Vektor liegt dann ungenutzt bereit, bis der Schalter umgelegt wird.
