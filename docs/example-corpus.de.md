# Beispielkorpus

Das Template liefert einen kleinen Korpus mit, damit es direkt nach dem Clonen
läuft — keine Datensuche, keine Konfiguration schreiben. Du bekommst zuerst einen
funktionierenden Chatbot und tauschst dann deine eigenen Dokumente ein.

## Was enthalten ist

Drei Open-Access-Artikel in `apps/chainlit/data/documents/`, alle in
*Scientific Reports* unter **CC BY 4.0** veröffentlicht:

| Datei | Artikel | Referenz |
|---|---|---|
| `Kage_2018_SciReports.pdf` | Luminescence lifetime encoding in time-domain flow cytometry | *Sci Rep* **8**, 16715 (2018), DOI [10.1038/s41598-018-35137-5](https://doi.org/10.1038/s41598-018-35137-5) |
| `Schmidt_2022_SciReports.pdf` | A multiparametric fluorescence assay for screening aptamer–protein interactions based on microbeads | *Sci Rep* **12**, 2961 (2022), DOI [10.1038/s41598-022-06817-0](https://doi.org/10.1038/s41598-022-06817-0) |
| `Lin_2024_SciReports.pdf` | Coupling a recurrent neural network to SPAD TCSPC systems for real-time fluorescence lifetime imaging | *Sci Rep* **14**, 3286 (2024), DOI [10.1038/s41598-024-52966-9](https://doi.org/10.1038/s41598-024-52966-9) |

Die vollständige Attribution (Titel, Autor:innen, Lizenzlinks) steht in
`apps/chainlit/data/documents/SOURCES.md` — diese Datei ist die maßgebliche
Quellenangabe und wird hier deshalb nicht dupliziert.

## Die zugehörige Instanz

Der Korpus gehört zu `examples/papers/rag.config.yaml` (Collection `papers`), der
Referenzinstanz mit allen Features eingeschaltet:

- fünf agentische [Tools](tools.md) (`search`, `list_documents`, `fetch_document`,
  `expand_context`, `verify_claim`);
- `chunking.strategy: semantic` — teilt an Bruchstellen der Embedding-Ähnlichkeit;
- `images.mode: describe` mit `inline_figures` — Abbildungen erhalten durchsuchbare
  Beschreibungen und werden über dem Absatz gezeigt, der sie zitiert (siehe
  [Abbildungen](images.md)).

`.env.example` und `docker-compose.yml` zeigen bereits darauf, `make up` startet
also unverändert genau diese Instanz.

**Warum diese Paper:** Sie enthalten echte Tabellen und Abbildungen. Damit lassen
sich Tabellen-Serialisierung, Figure-Beschreibungen und Mehr-Dokument-Retrieval gut
demonstrieren — Dinge, die ein reiner Prosa-Korpus nicht ausreizen würde.

## Eigenen Korpus einsetzen

1. **Eigene PDFs nach `apps/chainlit/data/documents/` legen.** Eigene Dateien sind
   gitignored (nur die drei Beispiele stehen auf einer Allowlist), erscheinen also
   nie in `git status`. Die Beispiele dürfen gelöscht werden — kein Code hängt an
   ihnen.

    ```bash
    cp ~/my-papers/*.pdf apps/chainlit/data/documents/
    ```

2. **Config kopieren und eine neue Collection setzen.** `vector_store.collection`
   zu ändern ist **Pflicht** — sonst mischen sich alte und neue Vektoren in
   derselben Collection. Passe gleich auch `prompt.starter_questions` an, denn die
   mitgelieferten nennen die Beispiel-Paper.

    ```bash
    cp examples/papers/rag.config.yaml my-rag.yaml
    ```
    ```yaml
    vector_store:
      collection: my_papers        # neuer Name — `papers` nicht wiederverwenden

    prompt:
      starter_questions:
        - "Welche Dokumente sind in der Wissensbasis?"
    ```

3. **Dry-Run, dann Ingestion.** `--dry-run` parst und chunkt ohne zu embedden, du
   kannst das Ergebnis also prüfen, bevor Embeddings Kosten verursachen.

    ```bash
    RAG_CONFIG=my-rag.yaml python -m kb.ingest --dry-run
    RAG_CONFIG=my-rag.yaml python -m kb.ingest             # oder --recreate
    ```

Danach die App wie gewohnt starten. Formatoptionen, Chunking-Strategien und die
Verdrahtung der Zitate stehen in [Daten hinzufügen](adding-data.md); den
vollständigen Durchlauf für den ersten Start in
[Erste Schritte](getting-started.md).

!!! note "Wechsel des Embedding-Modells"
    Eine Collection hält fest, mit welchem Embedding-Modell sie gebaut wurde. Wenn
    du `models.embed_model` änderst, ingeste erneut mit `--recreate` oder nimm eine
    neue Collection — die App weist eine Collection mit abweichendem
    Embedding-Modell ab, da die Vektoren inkompatibel wären.
