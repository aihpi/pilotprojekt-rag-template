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

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator

if TYPE_CHECKING:  # avoid import cycles at runtime
    from config.schema import DataSourceConfig, RagConfig


@dataclass
class Section:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    doc_id: str | None = None


# A parser takes the source config + the full config and returns its sections.
ParserFn = Callable[["DataSourceConfig", "RagConfig"], list[Section]]


# --------------------------------------------------------------------------- #
# Incremental ingest: the file gate
# --------------------------------------------------------------------------- #
# The ingest pipeline needs to know which files a run would read, and to skip the
# ones already indexed, without changing ``ParserFn`` — that signature is a
# documented extension point, so widening it would break every custom parser.
# Every built-in parser enumerates through ``iter_source_files``, so the gate
# lives there and a context variable carries it. Parsers that do not use the
# helper simply get no filtering: correct, just not cheap, because point ids are
# deterministic and upserts are idempotent.
_FILE_GATE: ContextVar["FileGate | None"] = ContextVar("_FILE_GATE", default=None)

_HASH_CHUNK_BYTES = 1 << 20


def file_digest(path: Path) -> str:
    """SHA-256 of a file's bytes, read in chunks so large PDFs stay cheap."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class FileGate:
    """Records every candidate file and hides the ones already ingested.

    ``known`` maps a gate key to the sha256 recorded at the last ingest. A file
    is skipped only when its hash still matches, so an *edited* document is
    re-ingested instead of going stale.

    ``skip_all`` enumerates and hashes without returning anything, which is how a
    run discovers the full file list while parsing nothing.
    """

    known: dict[str, str] = field(default_factory=dict)
    skip_all: bool = False
    root: Path | None = None
    seen: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def key(self, path: Path) -> str:
        """Stable identity for a file: relative to ``root`` when possible."""
        resolved = path.resolve()
        if self.root:
            try:
                return resolved.relative_to(self.root.resolve()).as_posix()
            except ValueError:  # outside the config dir — fall back to the full path
                pass
        return resolved.as_posix()

    def admit(self, paths: list[Path]) -> list[Path]:
        """Record each file, then return only those that need parsing."""
        admitted: list[Path] = []
        for path in paths:
            gate_key = self.key(path)
            try:
                digest = file_digest(path)
            except OSError as exc:  # unreadable: let the parser report it
                print(f"[ingest] could not hash {path.name}: {exc}")
                admitted.append(path)
                continue
            self.seen[gate_key] = digest
            if self.skip_all or self.known.get(gate_key) == digest:
                self.skipped.append(gate_key)
                continue
            admitted.append(path)
        return admitted


@contextmanager
def file_gate(gate: FileGate | None) -> Iterator[FileGate | None]:
    """Apply ``gate`` to every ``iter_source_files`` call in this context."""
    token = _FILE_GATE.set(gate)
    try:
        yield gate
    finally:
        _FILE_GATE.reset(token)


def iter_source_files(
    base: Path,
    glob: str | None,
    default_glob: str,
) -> list[Path]:
    """Return the files a source points at (a single file or a globbed dir).

    Under an active :func:`file_gate` the result excludes files whose contents
    are already indexed. Every candidate is recorded first, so the caller still
    learns about skipped files.
    """
    if base.is_file():
        found = [base]
    elif base.is_dir():
        found = sorted(p for p in base.glob(glob or default_glob) if p.is_file())
    else:
        return []

    gate = _FILE_GATE.get()
    return gate.admit(found) if gate else found
