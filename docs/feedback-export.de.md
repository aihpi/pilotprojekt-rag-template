# Feedback-Persistierung & CSV-Export

## Überblick

Chainlit zeigt in der Chat-UI Feedback-Buttons (Helpful / Not helpful plus
optionaler Freitext-Kommentar). Ohne einen registrierten
`@cl.on_feedback`-Handler werden diese Klicks jedoch **nicht** in der Datenbank
gespeichert.

Das Template ergänzt deshalb zwei Bausteine:

1. **Persistierung** — Feedback wird beim Klick in die PostgreSQL-`Feedback`-Tabelle geschrieben.
2. **CSV-Export** — ein authentifizierter HTTP-Endpoint liefert alle Feedback-Daten aller Nutzer als CSV-Download.

## Beteiligte Dateien

### `apps/chainlit/native_chat.py`

| Funktion | Beschreibung |
|---|---|
| `upsert_feedback()` | Speichert Feedback in die PostgreSQL-`Feedback`-Tabelle. Nutzt `ON CONFLICT ("stepId")` statt `ON CONFLICT (id)`, damit wiederholte Klicks auf denselben Step das bestehende Feedback aktualisieren statt Duplikate zu erzeugen. Legt den Unique Index `Feedback_stepId_unique` idempotent an. |
| `export_feedback_csv()` | Exportiert alle Feedback-Daten als CSV. Löst per `LEFT JOIN LATERAL` die korrekte Assistenz-Antwort auf (Child-Step vom Typ `assistant_message`, da Feedback an `run`-Steps hängt, die selbst keinen Output haben). Ebenso wird die vorangehende Nutzerfrage per LATERAL JOIN ermittelt. |

### `apps/chainlit/app.py`

| Baustein | Beschreibung |
|---|---|
| `@cl.on_feedback`-Handler | Persistiert Feedback-Events (Helpful/Not helpful + Kommentar) über `upsert_feedback()` in PostgreSQL. Ohne diesen Handler zeigt Chainlit die Buttons, speichert aber nichts. |
| `GET /export/feedback` | Authentifizierter Endpoint, liefert einen CSV-Download aller Feedback-Daten aller Nutzer. |
| Imports | `export_feedback_csv` und `upsert_feedback` aus `native_chat`. |

## DB-Schema-Änderung

- **Unique Index** `Feedback_stepId_unique` auf `"Feedback"."stepId"` — verhindert doppelte Feedback-Einträge pro Step.
- Der Index wird beim ersten Aufruf von `upsert_feedback()` idempotent via `CREATE UNIQUE INDEX IF NOT EXISTS` angelegt.

## Datenmodell-Hintergrund

Chainlit hängt Feedback an **`run`-Steps** (den `on_message`-Wrapper), nicht an
`assistant_message`-Steps. `run`-Steps haben jedoch keinen eigenen Output — die
eigentliche Antwort steckt in einem **Child-Step** vom Typ `assistant_message`
mit `parentId = run.id`.

Die Export-Query löst das so auf:

```sql
LEFT JOIN LATERAL (
    SELECT cs.output FROM "Step" cs
    WHERE cs."parentId" = s.id AND cs.type = 'assistant_message'
    ORDER BY cs."startTime" DESC LIMIT 1
) child ON true
```

und verwendet `COALESCE(child.output, s.output)` als Fallback.

## CSV-Spalten

| Spalte | Beschreibung |
|---|---|
| `username` | Benutzername (aus `User.identifier` via Thread) |
| `user_question` | Die Nutzerfrage (vorhergehender `user_message`-Step) |
| `assistant_answer` | Die Assistenz-Antwort (Child-`assistant_message`-Step) |
| `feedback_value` | `1.0` = Helpful, `0.0` = Not helpful |
| `feedback_comment` | Optionaler Freitext-Kommentar |
| `answer_time` | Zeitstempel der Antwort (ISO 8601) |
| `thread_id` | UUID des Chat-Threads |
| `feedback_id` | UUID des Feedback-Eintrags |
| `step_id` | UUID des Steps, an den das Feedback gehängt wurde |

## Nutzung

### CSV-Export über den Browser

```
http://localhost:8000/export/feedback
```

Erfordert Authentifizierung (Login-Cookie oder OAuth-Token) und liefert die
Feedback-Daten aller Nutzer.

### Ad-hoc-Abfrage über PostgreSQL

```bash
docker exec rag-postgres psql -U chainlit -d chainlit -c '
SELECT u.identifier, f.value, f.comment, s."createdAt"
FROM "Feedback" f
JOIN "Step" s ON s.id = f."stepId"
JOIN "Thread" t ON t.id = s."threadId"
LEFT JOIN "User" u ON u.id = t."userId"
ORDER BY s."createdAt" DESC;
'
```

!!! note "Voraussetzung: PostgreSQL"
    Persistierung und Export brauchen den PostgreSQL-Datenlayer aus dem
    Compose-Stack (`make up`, siehe [Erste Schritte](getting-started.md)). Ohne
    `DATABASE_URL` zeigt Chainlit die Feedback-Buttons, es gibt aber keinen
    Speicherort und damit nichts zu exportieren.
