"""Watch the document folders and index changes without being asked.

Dropping a file into the documents folder should be enough. Nothing else has to
be run, which is only practical because ingestion became incremental: a pass that
finds nothing new costs a handful of stat calls and one small read from Qdrant.

Polling, not filesystem events. Event delivery from a host bind mount into a
container is unreliable on Docker Desktop, and a watcher that silently stops
noticing changes is worse than one that looks every few seconds.

Two stages, deliberately. The cheap stage compares size and modification time, so
the common case (nothing changed) reads no file contents at all. Only when that
hints at a change does the real, authoritative run start, which hashes contents
and decides what actually needs work.
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

    async with _lock:
        result = await _ingest_now()
    print(f"[watch] done: {_describe(result)}")

    # Re-read afterwards: describing figures can take minutes, and anything that
    # arrived meanwhile should count as seen only once an ingest picked it up.
    return _settled(await asyncio.to_thread(_snapshot), time.time_ns())


async def watch_documents() -> None:
    """Poll the document folders forever, indexing whatever changed."""
    previous: dict[str, str] | None = None
    while True:
        try:
            previous = await run_pass(previous)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a watcher must not die on one error
            print(f"[watch] pass failed, will retry: {type(exc).__name__}: {exc}")
        await asyncio.sleep(DOCUMENT_WATCH_INTERVAL)
