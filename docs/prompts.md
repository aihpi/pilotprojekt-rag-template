# System Prompts

The system prompt is the standing instruction the assistant follows in every
conversation. It is what turns a search engine into *your* assistant: it says who
the assistant is, insists it looks things up before answering, and fixes the
exact wording of sources and follow-up questions so the app can turn them into
clickable links and buttons.

You can write it yourself, or let the app write one for you based on your own
documents.

## Where the prompt comes from

At startup the app looks in three places, in this order, and uses the first one
it finds:

| Order | Source | When it applies |
|---|---|---|
| 1 | `prompt.system_prompt_path` | You named a file **and** that file exists |
| 2 | Auto-generation | `prompt.auto_generate: true` (the default): the app writes one at startup |
| 3 | `apps/chainlit/config/prompts/default_system.md` | The one supplied with the project, used if neither of the above produced anything |

Because of that order, naming your own file switches the automatic writing off
completely. Your file always wins.

```yaml
prompt:
  auto_generate: true
  template_path: ../../system.md      # optional
  sample_size: 40
  starter_questions: ["...", "..."]
  # system_prompt_path: ./my_prompt.md  # explicit -> no generation
```

## Auto-generation

The app does not guess what your documents are about. It reads them:

1. It takes a sample of the text **actually stored** for your documents: the
   section headings of each one plus a few typical excerpts. `prompt.sample_size`
   limits how much is used (default `40`).
2. It loads a skeleton that provides the structure and the strictness, but no
   wording about your subject. By default this is a `system.md` next to the
   project if one exists, otherwise the one supplied with it. You can point
   somewhere else with `prompt.template_path`.
3. It asks your chat model to write the instruction from those two pieces.

The result is saved next to your settings file as
`.generated_system_prompt.<collection>.md` and reused on every restart. So this
costs one model call per set of documents, not one per start.

The generated instruction covers:

- who the assistant is, worked out from your documents;
- the duty to look things up first and answer only from what was found;
- the exact format for sources;
- the language to answer in;
- a maximum length;
- the format for follow-up questions.

To have a new one written, delete the saved file or use the
`REGENERATE_SYSTEM_PROMPT` setting:

```bash
rm .generated_system_prompt.papers.md
# or:
REGENERATE_SYSTEM_PROMPT=true chainlit run app.py
```

!!! warning "Citations and follow-ups are parsed with German markers"
    The app recognises sources and follow-up questions only by their **German**
    wording: sources as `Quelle N: <section> (S.<page>)` and follow-ups under the
    heading `Anschlussfragen:`. So if you want clickable sources and follow-up
    buttons, set `language: de`.

    Your **documents** can be in any language. The model happily reads English
    papers and answers in German. If you write the instruction yourself, keep
    those two pieces of wording exactly as they are, or the links and buttons stop
    appearing.

## Viewing and editing the prompt at runtime

The gear panel shows the active instruction in an editable box
("System-Prompt (bearbeitbar)"). Edits there apply **only to you and only in this
session**, and clearing the box returns to the normal one.

That makes the panel perfect for trying out wording, but it is not a way to roll
a change out to everyone. For a permanent change, edit the prompt file, or point
`prompt.system_prompt_path` at a file of your own.

## Choosing the chat model

The same gear panel has a model selector. It fills itself with whatever your AI
service offers (search models are hidden, since they cannot chat). Some services
do not publish a list, and then the selector stays empty. In that case write the
names in yourself:

```yaml
models:
  chat_model: gpt-oss-120b
  selectable_chat_models: []   # merged with whatever /v1/models advertises
```

Each person's choice is remembered, so it also applies to their new chats.

!!! note "Related pages"
    [Getting Started](getting-started.md) for the first run,
    [Configuration Reference](configuration.md) for every `prompt:` field, and
    [Agentic Tools](tools.md) for the abilities the instruction tells the model
    to use.
