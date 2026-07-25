"""Parser interface: a data source -> a list of :class:`Section`.

A parser owns its own file iteration (globbing a directory or reading a single
file) so format-specific logic (e.g. the Docling-JSON section reconstruction,
which spans a whole directory) stays inside the parser.

``Section.metadata`` uses the flat keys the retrieval/citation layer reads
(``source_file``/``file``/``source``, ``title``/``section_title``,
``page_start``/``page_end``, plus arbitrary extras). ``doc_id`` is a stable
string; the ingestion pipeline derives each Qdrant point id from it, so keeping
it deterministic keeps re-ingests idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

if TYPE_CHECKING:  # avoid import cycles at runtime
    from config.schema import DataSourceConfig, RagConfig


@dataclass
class Section:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    doc_id: str | None = None


# A parser takes the source config + the full config and returns its sections.
ParserFn = Callable[["DataSourceConfig", "RagConfig"], list[Section]]


def iter_source_files(
    base: Path,
    glob: str | None,
    default_glob: str,
) -> list[Path]:
    """Return the files a source points at (a single file or a globbed dir)."""
    if base.is_file():
        return [base]
    if base.is_dir():
        return sorted(p for p in base.glob(glob or default_glob) if p.is_file())
    return []
