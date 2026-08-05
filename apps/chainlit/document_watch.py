"""Watch the document folders and index changes without being asked.

Dropping a file into the documents folder should be enough. Nothing else has to
be run, which is only practical because ingestion became incremental: a pass that
finds nothing new costs a handful of stat calls and one small read from Qdrant.

Filesystem events, via watchfiles. I first assumed events would not cross a Docker
Desktop bind mount and built this on polling; testing an inotify watch inside the
container against writes from the host disproved that. Detection went from up to 20
seconds to about 0.3.

Two stages, deliberately. The events only say "something happened" and are otherwise
ignored; the authoritative comparison is a size and modification time sweep, which
reads no file contents, and only a real difference starts a run that hashes and
decides. So a missed, duplicated or coalesced event costs at most one extra 2 ms
sweep, and there is no event bookkeeping to get wrong.
"""

from __future__ import annotations

import asyncio
from typing import Any

from config import get_config
from kb.parsers.base import FileGate
from settings import (
    DOCUMENT_WATCH_INTERVAL,
    DOCUMENT_WATCH_SETTLE,
)

# One pass at a time. A second pass starting while the first is still describing
# figures would duplicate work and could interleave manifest writes.
_lock = asyncio.Lock()

# What the UI shows. A background task has no Chainlit session, so it cannot push a
# message to anyone; the browser polls `/ingest-status` for this instead. A plain
# dict is enough: single process, single writer, and readers only ever see a whole
# replacement because the assignment is atomic.
_status: dict[str, Any] = {"state": "idle", "message": "", "revision": 0}


def get_status() -> dict[str, Any]:
    """Current indexing state, for the status endpoint."""
    return dict(_status)


def _set_status(state: str, message: str, **extra: Any) -> None:
    global _status
    # The revision lets the browser tell "finished indexing" from "finished
    # indexing again" when both carry the same text, so a second run still shows.
    _status = {
        "state": state,
        "message": message,
        "revision": _status.get("revision", 0) + 1,
        **extra,
    }


def _snapshot() -> dict[str, str]:
    """Cheap `{file: "size:mtime"}` map of every configured source file.

    Goes through ``plan_ingest`` with a stat-only gate rather than re-deriving the
    file lists here. The parsers own their file enumeration (including the
    docling-JSON directory, which does not live under ``path``), so duplicating
    that logic would drift out of step with them.
    """
    from kb.ingestion_pipeline import plan_ingest

    config = get_config()
    gate = FileGate(skip_all=True, stat_only=True, root=config.resolve_path("."))
    plan_ingest(config, gate=gate)
    return dict(gate.seen)


def _settled(snapshot: dict[str, str], now_ns: int) -> dict[str, str]:
    """Drop files written in the last few seconds.

    A large PDF being copied in is visible long before it is complete. Hashing it
    mid-write would index a truncated document, and while the next pass would
    repair it once the hash changed, with ``images.mode: describe`` that repair
    costs a vision call per figure. Waiting a moment is cheaper than being right
    twice.
    """
    settle_ns = DOCUMENT_WATCH_SETTLE * 1_000_000_000
    settled = {}
    for key, token in snapshot.items():
        _, _, mtime = token.partition(":")
        try:
            if now_ns - int(mtime) >= settle_ns:
                settled[key] = token
        except ValueError:  # malformed token, treat as settled rather than stall
            settled[key] = token
    return settled


async def _ingest_now() -> dict[str, Any]:
    """Run a real incremental ingest off the event loop.

    Parsing a PDF is synchronous and slow, so running it on the main loop would
    freeze every open chat for the duration. ``ingest_all`` is a coroutine, hence
    ``asyncio.run`` inside the worker thread.
    """

    def run() -> dict[str, Any]:
        from kb.ingestion_pipeline import ingest_all

        return asyncio.run(ingest_all(get_config()))

    return await asyncio.to_thread(run)


_MAX_LISTED_FILES = 12


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _file_labels(added: list[str], changed: list[str], gone: list[str]) -> list[str]:
    """`[{"name": ..., "action": ...}]` for the hover panel, newest concern first."""
    labels: list[dict[str, str]] = []
    for action, keys in (("removed", gone), ("new", added), ("changed", changed)):
        for key in keys:
            labels.append({"name": key.rsplit("/", 1)[-1], "action": action})
    if len(labels) > _MAX_LISTED_FILES:
        remaining = len(labels) - _MAX_LISTED_FILES
        labels = labels[:_MAX_LISTED_FILES]
        labels.append({"name": f"and {remaining} more", "action": "more"})
    return labels


def _working_message(added: int, edited: int, removed: int) -> str:
    """Say what is happening in the user's terms, not in ours.

    Deleting is called out separately: it is the one action people worry about, so
    "removing" should never be hidden behind a generic "indexing".
    """
    parts = []
    if added:
        parts.append(f"{_plural(added, 'new document', 'new documents')}")
    if edited:
        parts.append(f"{_plural(edited, 'changed document', 'changed documents')}")
    if removed:
        parts.append(f"removing {_plural(removed, 'document', 'documents')}")
    if not parts:
        return "Change detected. Indexing..."
    return "Change detected: " + ", ".join(parts) + "..."


def _done_message(result: dict[str, Any]) -> str:
    parts = []
    if result.get("ingested"):
        parts.append(f"{_plural(result['ingested'], 'passage', 'passages')} indexed")
    if result.get("pruned"):
        parts.append(f"{_plural(result['pruned'], 'passage', 'passages')} removed")
    return "Done: " + ", ".join(parts) if parts else "Done. Nothing to change."


def _describe(result: dict[str, Any]) -> str:
    parts = []
    if result.get("ingested"):
        parts.append(f"{result['ingested']} chunk(s) indexed")
    if result.get("pruned"):
        parts.append(f"{result['pruned']} entr(ies) removed")
    if result.get("adopted"):
        parts.append(f"{result['adopted']} file(s) adopted")
    return ", ".join(parts) or "no change"


async def run_pass(previous: dict[str, str] | None) -> dict[str, str]:
    """One poll: look, and index if anything changed. Returns the new baseline.

    Separate from the loop on purpose. Everything worth testing lives here, and it
    can be called directly, so tests do not have to drive an event loop and guess
    how many times to yield.
    """
    import time

    current = _settled(await asyncio.to_thread(_snapshot), time.time_ns())
    if previous is None:
        # First pass only learns the current state. The startup ingest has already
        # run by now, so acting here would just repeat it.
        return current
    if current == previous:
        return previous

    added = sorted(set(current) - set(previous))
    gone = sorted(set(previous) - set(current))
    changed = sorted(k for k in set(current) & set(previous) if current[k] != previous[k])
    print(
        f"[watch] documents changed (new: {len(added)}, edited: {len(changed)}, "
        f"removed: {len(gone)}); indexing"
    )
    for label, keys in (("new", added), ("edited", changed), ("removed", gone)):
        for key in keys:
            print(f"[watch]   {label}: {key}")

    _set_status(
        "working",
        _working_message(len(added), len(changed), len(gone)),
        added=len(added),
        edited=len(changed),
        removed=len(gone),
        # File names, so hovering shows which documents rather than just a count.
        # Basenames only: the full path is noise, and capped so a bulk import does
        # not produce an unreadable list.
        files=_file_labels(added, changed, gone),
    )
    try:
        async with _lock:
            result = await _ingest_now()
    except Exception:
        _set_status("error", "Indexing failed. See the app log for details.")
        raise
    print(f"[watch] done: {_describe(result)}")
    _set_status(
        "done",
        _done_message(result),
        files=_file_labels(added, changed, gone),
        indexed=result.get("ingested", 0),
        removed_passages=result.get("pruned", 0),
    )

    # Re-read afterwards: describing figures can take minutes, and anything that
    # arrived meanwhile should count as seen only once an ingest picked it up.
    return _settled(await asyncio.to_thread(_snapshot), time.time_ns())


def _watch_dirs() -> list[str]:
    """The directories the configured sources read from."""
    config = get_config()
    dirs = []
    for src in config.data_sources:
        path = config.resolve_path(src.path)
        path = path if path.is_dir() else path.parent
        if path.is_dir():
            dirs.append(str(path))
    # ponytail: a source using pdf_options.docling_json_dir points somewhere else and
    # is not watched; the timeout tick below still picks it up. Add it here if anyone
    # actually uses that option with live updates.
    return sorted(set(dirs))


async def watch_documents() -> None:
    """Index whatever changes in the document folders.

    The events are deliberately ignored: any of them, or a timeout tick, just runs
    ``run_pass``, which does the authoritative size+mtime comparison anyway. So a
    missed, duplicated or coalesced event costs at most one extra 2 ms sweep, and
    there is no event-handling logic to get wrong.

    The timeout tick is not belt-and-braces, it is required. ``_settled`` holds back
    files younger than DOCUMENT_WATCH_SETTLE, so a file written now fires its event
    now, gets held back as too fresh, and no further event follows. The tick collects
    it. It also covers anything the events miss.
    """
    from watchfiles import awatch

    dirs = _watch_dirs()
    if not dirs:
        print("[watch] no document folders to watch")
        return
    print(f"[watch] watching {', '.join(dirs)}")

    previous: dict[str, str] | None = None
    async for _ in awatch(
        *dirs,
        rust_timeout=DOCUMENT_WATCH_INTERVAL * 1000,
        yield_on_timeout=True,
    ):
        try:
            previous = await run_pass(previous)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a watcher must not die on one error
            print(f"[watch] pass failed, will retry: {type(exc).__name__}: {exc}")
