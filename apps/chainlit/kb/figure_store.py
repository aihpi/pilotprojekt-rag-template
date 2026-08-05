"""Figure image persistence + safe path resolution, shared by ingest, query, and UI.

When ``images.mode != none`` the PDF parser saves each rendered figure here and
records the returned filename on the figure chunk's metadata (``image_path``).
The query/UI layers read it back to attach pixels to a vision call and to render
side-panel thumbnails. Keeping this in one module avoids duplicating the
path-containment logic (mirrors ``app._resolve_source_pdf_path``).
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config.schema import RagConfig


def figure_dir(config: "RagConfig") -> Path:
    """Directory where figure PNGs live: ``images.figure_store_dir`` if set,
    else ``<sources.data_dir>/figures`` (both resolved relative to the config)."""
    configured = config.images.figure_store_dir
    if configured:
        return config.resolve_path(configured)
    return config.resolve_path(config.sources.data_dir) / "figures"


def figure_filename(stem: str, fig_idx: int) -> str:
    """Deterministic per-figure filename → re-ingest overwrites in place."""
    return f"{stem}__fig{fig_idx}.png"


def persist_figure(pil_image: Any, stem: str, fig_idx: int, dest_dir: Path) -> str:
    """Save a PIL image as PNG under ``dest_dir``; return the bare filename to
    store in chunk metadata."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = figure_filename(stem, fig_idx)
    pil_image.save(dest_dir / name, "PNG")
    return name


def description_dir(config: "RagConfig") -> Path:
    """Directory holding the written figure descriptions: ``<sources.data_dir>/descriptions``.

    Anchored to ``data_dir``, the folder people actually manage, rather than derived
    from :func:`figure_dir`. A custom ``images.figure_store_dir`` may point anywhere,
    so deriving from it would scatter descriptions outside the corpus and break the
    documented cleanup in docs/managing-documents.md.
    """
    return config.resolve_path(config.sources.data_dir) / "descriptions"


_DESCRIBE_STORE_WARNED = False


def describe_figure(
    image_data_uri: str,
    prompt: str,
    model: str,
    *,
    descriptions: Path,
    stem: str,
    fig_idx: int,
) -> str:
    """Describe a figure, reusing ``descriptions/<paper>/fig<n>.md`` when it still fits.

    The description is a durable artefact of the corpus, like the figure PNG beside
    it, not a cache: it is readable, it belongs to one paper, and it is deleted by
    deleting that paper's folder. Re-reading documents therefore does not pay for
    vision calls again, which matters for ``--recreate`` and for an import that died
    partway.

    The stored ``key`` fingerprints the encoded image, the prompt and the model, so
    editing ``describe_prompt``, switching ``vision_model`` or changing
    ``describe_image_max_px`` regenerates instead of serving stale text.

    Keyword-only after ``model`` because the last three are easy to transpose, and a
    swap would silently write to ``descriptions/0/figPaper.md``.
    """
    global _DESCRIBE_STORE_WARNED
    from llm import describe_image_sync

    key = hashlib.sha256("\0".join((image_data_uri, prompt, model)).encode()).hexdigest()
    path = descriptions / stem / f"fig{fig_idx}.md"
    try:
        _, front, body = path.read_text("utf-8").split("---\n", 2)
        if front.strip().removeprefix("key:").strip() == key and (text := body.strip()):
            return text
    except (OSError, ValueError):
        # ValueError covers a missing second "---" and bytes that are not UTF-8
        # (UnicodeDecodeError). Either way: describe it again rather than raise, or
        # one mangled file aborts the whole import.
        pass

    description = describe_image_sync(image_data_uri, prompt, model)
    if description:
        # Random suffix, not os.getpid(): every container runs as PID 1, so two
        # concurrent ingests would otherwise interleave into one temp path.
        tmp = path.with_name(f"fig{fig_idx}.{os.urandom(6).hex()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Hand-written rather than yaml.safe_dump: a sha256 hex needs no escaping,
            # and reading it back needs no parser either.
            tmp.write_text(f"---\nkey: {key}\n---\n{description}\n", "utf-8")
            os.replace(tmp, path)
        except (OSError, ValueError) as exc:
            # ValueError catches UnicodeEncodeError, which a lone surrogate from the
            # model would raise. Warn once, not once per figure: a corpus that cannot
            # store descriptions silently re-pays for every vision call on every run,
            # which is exactly the cost this function exists to avoid.
            if not _DESCRIBE_STORE_WARNED:
                _DESCRIBE_STORE_WARNED = True
                print(
                    f"[ingest] cannot store figure descriptions in {path.parent} "
                    f"({type(exc).__name__}: {exc}); they will be requested again "
                    "on every run."
                )
        finally:
            tmp.unlink(missing_ok=True)  # a crash before os.replace must not litter
    return description


def resolve_figure_path(file_name: str, base: Path) -> Path | None:
    """Resolve a stored figure filename to a path under ``base``, rejecting path
    traversal and non-PNG/non-existent files (mirrors the PDF route's guard)."""
    if not isinstance(file_name, str) or not file_name:
        return None
    if "/" in file_name or "\\" in file_name or ".." in file_name:
        return None
    path = (base / file_name).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return None
    if path.is_file() and path.suffix.lower() == ".png":
        return path
    return None


def file_to_data_uri(path: Path, max_px: int | None = None, quality: int = 80) -> str:
    """Read a figure into an OpenAI-style ``data:image/...;base64,...`` URI.

    With ``max_px`` set (the vision-call path), downscale so the longest side ≤
    ``max_px`` and re-encode as JPEG — figures are stored full-res as PNG for
    display, but the vision request must stay under the gateway's body-size limit
    (attaching several full-res PNGs triggers HTTP 413; JPEG is far smaller)."""
    if max_px:
        from io import BytesIO

        from PIL import Image

        img = Image.open(path)
        img.thumbnail((max_px, max_px))
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, "JPEG", quality=quality)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def pil_to_data_uri(pil_image: Any, max_px: int | None = None, quality: int = 80) -> str:
    """Encode a PIL image directly to a data URI (used at ingest).

    Same reasoning as :func:`file_to_data_uri`: with ``max_px`` set, downscale so
    the longest side ≤ ``max_px`` and re-encode as JPEG, because a full-res PNG
    figure can exceed the gateway's body-size limit and come back as HTTP 413.
    The describe step used to send raw PNG and silently lost those figures.
    Without ``max_px`` the original PNG behaviour is kept."""
    from io import BytesIO

    buf = BytesIO()
    if max_px:
        # .copy() because thumbnail() resizes in place, and the caller still owns
        # this image (it persists the full-res PNG from the same object).
        img = pil_image.copy()
        img.thumbnail((max_px, max_px))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, "JPEG", quality=quality)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    pil_image.save(buf, "PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
