# Figures & images

Without this feature the PDF parser **drops figures entirely** — only their
captions survive, as text. That is a real loss for papers and handbooks whose
argument lives in a diagram. Turn figures into first-class, searchable content
with the `images:` block.

```yaml
images:
  mode: none            # none | describe | attach
  vision_model: gpt-4o-mini
  images_scale: 2.0     # Docling render scale (2.0 = higher-res crops)
  inline_figures: true
  show_unmarked_figures: true
  inline_figure_caption: false
  max_attach_images: 3
  attach_image_max_px: 768
  vision_capable_models: [gpt-4o-mini, gpt-4o]
```

The mode can be switched without touching the YAML via the `IMAGES_MODE`
environment variable — handy for A/B comparisons of the same corpus.

## Three modes

| Mode | At ingest | At query time |
|---|---|---|
| `none` | Figures ignored | — (cheapest) |
| `describe` | Each figure is rendered and described by `vision_model`, stored as its own chunk | Description is searched and cited like any text chunk |
| `attach` | Same as `describe` | Plus: the figure **pixels** are sent to a vision-capable chat model |

In `describe`, every figure becomes a normal, searchable and citable chunk whose
text is the model-written description, in the language of the instance. Its
metadata carries `is_figure`, `figure_index`, `image_path` and the page. Because
retrieval then only ever touches text, `describe` works with **any** chat model
afterwards — the vision model is needed at ingest, not at query time.

`attach` builds on exactly the same ingest and adds a vision pass to the answer.

## Cost, and when you must re-ingest

The rule is simple: **one vision call per figure, at ingest.** Nothing is spent
per query in `describe`.

!!! warning "Switching modes"
    Going from `none` to `describe` or `attach` requires a **re-ingest** — the
    figures were never rendered or described. Switching between `describe` and
    `attach` does **not**: the images and descriptions are already stored, only
    the query-time behavior changes. See
    [Adding your data](adding-data.md) for the re-ingest flags.

## Showing figures in the answer

Two independent switches decide what the user actually sees:

| `inline_figures` | `show_unmarked_figures` | Result |
|---|---|---|
| `true` | `true` | Marked figure appears **above** the paragraph describing it; all other retrieved figures as thumbnails below the answer (default) |
| `true` | `false` | Only figures the model actually marked appear — cleanest, but nothing is shown if it forgets the marker |
| `false` | `true` | No inline images; every retrieved figure as a thumbnail below the answer |
| `false` | `false` | No figures displayed at all — descriptions stay searchable and citable |

`inline_figure_caption: true` additionally prints the figure caption as an italic
line under the inlined image.

## How the figure marker works

Worth understanding, because it explains both the good and the odd cases:

1. The retrieval context contains one extra line per figure:
   `Abbildungs-Marker: {{ABB:<file name>}}`.
2. A per-request system instruction asks the model to copy that marker verbatim
   onto its own line, directly before the paragraph that describes the figure.
3. Answer post-processing replaces the marker with the actual image.

Markers that cannot be resolved are removed silently — users never see a raw
`{{ABB:…}}` — and the figure then simply shows up below the answer instead. The
instruction text itself is configurable via `images.figure_marker_prompt`.

## Where the images live

Figures are written as PNGs to `<sources.data_dir>/figures/` (override the path
with `images.figure_store_dir`) and served through an **authenticated** route,
`/sources/figure/<file>` — the same access rules as your source documents. The
folder is in `.gitignore` on purpose: it is regenerated on every ingest and does
not belong in the repository.

## `attach` needs a vision-capable chat model

The gateway exposes no per-model capability flag, so `images.vision_capable_models`
is authoritative. If the active chat model is not on that list, the app falls back
silently to the ordinary text answer and logs a warning — the answer is still
correct, just without the pixels.

Before the call each figure is downscaled so its longest side is at most
`attach_image_max_px` and sent as JPEG. Do not remove that: full-resolution
figures make gateways reject the request with **HTTP 413**. `max_attach_images`
caps how many figures a single answer may carry.

!!! note "A cosmetic quirk"
    Chainlit renders markdown images inside a 16:9 frame of limited width. Tall
    figures (portrait diagrams, stacked plots) therefore get letterbox margins on
    the sides. Nothing is cut off — clicking the image still opens it in full.
