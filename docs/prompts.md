# System Prompts

The system prompt is what turns a retrieval pipeline into *your* assistant: it
sets the identity, forces retrieval before answering, and fixes the citation and
follow-up format the UI parses. You can write it by hand, or let the app generate
one from your own corpus at startup.

## Where the prompt comes from

At startup the app resolves the prompt from three sources, in this order:

| Order | Source | When it applies |
|---|---|---|
| 1 | `prompt.system_prompt_path` | The field is set **and** the file exists |
| 2 | Auto-generation | `prompt.auto_generate: true` (the default) — one prompt is generated at startup |
| 3 | `apps/chainlit/config/prompts/default_system.md` | Bundled fallback, used when neither of the above yields a prompt |

Setting `prompt.system_prompt_path` therefore disables generation entirely — an
explicit file always wins.

```yaml
prompt:
  auto_generate: true
  template_path: ../../system.md      # optional
  sample_size: 40
  starter_questions: ["...", "..."]
  # system_prompt_path: ./my_prompt.md  # explicit -> no generation
```

## Auto-generation

Generation (`apps/chainlit/system_prompt_gen.py`) does not guess what your corpus
is about — it reads it:

1. It samples the chunks **actually indexed** in the active collection (section
   titles per document plus a few representative excerpts). `prompt.sample_size`
   caps how many are used (default `40`).
2. It loads a structural template — `prompt.template_path`, defaulting to a
   `system.md` at the repo root if one exists, otherwise the bundled fallback.
   The template supplies structure and rigor, not domain wording.
3. It asks the configured chat model to write a prompt from both.

The result is cached next to the config as
`.generated_system_prompt.<collection>.md` (gitignored) and reused on every
restart, so generation costs one model call per corpus, not one per boot.

The generated prompt contains:

- a domain-specific identity, inferred from the corpus;
- a retrieval-first obligation (call the tool, answer only from retrieved passages);
- the citation format the app parses;
- the answer language;
- a length limit;
- the follow-up-questions format.

To build a new one, delete the cache file or set `REGENERATE_SYSTEM_PROMPT=true`:

```bash
rm .generated_system_prompt.papers.md
# or:
REGENERATE_SYSTEM_PROMPT=true chainlit run app.py
```

!!! warning "Citations and follow-ups are parsed with German markers"
    The app currently detects citations and follow-up questions by their **German**
    markers: citations as `Quelle N: <section> (S.<page>)`, follow-ups under the
    header `Anschlussfragen:`. So if you want clickable citations and follow-up
    buttons, set `language: de`. The **corpus** may be in any language — the model
    reads the English papers and answers in German. If you write the prompt by
    hand, keep those two markers exactly.

## Viewing and editing the prompt at runtime

The gear panel shows the active prompt in an editable field
("System-Prompt (bearbeitbar)"). An edit there applies **per user/session** — it
is stored as `custom_prompt` and overrides the loaded prompt. Clearing the field
returns to the default.

That makes the panel ideal for iterating on wording; it is not a deployment
mechanism. For a permanent change, edit the prompt file or point
`prompt.system_prompt_path` at your own file.

## Choosing the chat model

The same gear panel carries a chat-model selector. Its list fills itself from the
gateway's `/v1/models` (embedding models are filtered out) and can be extended
via `models.selectable_chat_models` — useful when the gateway does not enumerate
its models:

```yaml
models:
  chat_model: gpt-oss-120b
  selectable_chat_models: []   # merged with whatever /v1/models advertises
```

The selection is stored per user in the database, so it also applies to new
chats.

!!! note "Related pages"
    [Getting Started](getting-started.md) for the first run,
    [Configuration Reference](configuration.md) for every `prompt:` field, and
    [Agentic Tools](tools.md) for the tools the prompt tells the model to call.
