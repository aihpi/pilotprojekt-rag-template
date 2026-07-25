"""Figure image persistence + safe path resolution, shared by ingest, query, and UI.

When ``images.mode != none`` the PDF parser saves each rendered figure here and
records the returned filename on the figure chunk's metadata (``image_path``).
The query/UI layers read it back to attach pixels to a vision call and to render
side-panel thumbnails. Keeping this in one module avoids duplicating the
path-containment logic (mirrors ``app._resolve_source_pdf_path``).
"""

from __future__ import annotations

import base64
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


def pil_to_data_uri(pil_image: Any) -> str:
    """Encode a PIL image directly to a PNG data URI (used at ingest)."""
    from io import BytesIO

    buf = BytesIO()
    pil_image.save(buf, "PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
