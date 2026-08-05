"""The folder watcher: cheap detection, settle delay, and one pass at a time.

The watcher exists so that putting a file into the documents folder is enough. It
reacts to filesystem events, with a slow timeout tick as the backstop, since the
settle rule holds back a file that was written a moment ago and no further event
follows it.

Detection is deliberately two-stage. Events are only a trigger; what decides is a
size and modification time sweep that reads no file contents, and only a real
difference leads to hashing.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("OAUTH_GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("OAUTH_GITHUB_CLIENT_SECRET", "test-client-secret")

import pytest  # noqa: E402

import document_watch  # noqa: E402
from config.schema import ChunkingConfig, DataSourceConfig, RagConfig  # noqa: E402
from kb.parsers.base import FileGate, file_gate, iter_source_files  # noqa: E402


def _config_at(dir_path, **kw) -> RagConfig:
    cfg = RagConfig(**kw)
    cfg._config_dir = dir_path
    return cfg


def _text_config(tmp_path) -> RagConfig:
    return _config_at(
        tmp_path,
        data_sources=[DataSourceConfig(name="docs", path="docs", format="txt", glob="*.txt")],
        chunking=ChunkingConfig(strategy="passthrough"),
    )


# --------------------------------------------------------------------------- #
# The cheap stage
# --------------------------------------------------------------------------- #
def test_stat_only_records_without_reading_contents(tmp_path, monkeypatch):
    """A poll must not hash. Proven by making hashing fail loudly."""
    target = tmp_path / "a.txt"
    target.write_text("content", encoding="utf-8")

    def explode(_path):
        raise AssertionError("stat_only must not hash file contents")

    monkeypatch.setattr("kb.parsers.base.file_digest", explode)
    gate = FileGate(skip_all=True, stat_only=True, root=tmp_path)

    assert gate.admit([target]) == []
    assert list(gate.seen) == ["a.txt"]
    assert ":" in gate.seen["a.txt"], "token should be size:mtime"


def test_stat_token_changes_when_the_file_changes(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("first", encoding="utf-8")
    before = FileGate(skip_all=True, stat_only=True, root=tmp_path)
    before.admit([target])

    target.write_text("a different length of content", encoding="utf-8")
    after = FileGate(skip_all=True, stat_only=True, root=tmp_path)
    after.admit([target])

    assert after.seen["a.txt"] != before.seen["a.txt"]


def test_a_vanished_file_does_not_crash_the_poll(tmp_path):
    """Listing and stat are not atomic; a file can disappear in between."""
    gate = FileGate(skip_all=True, stat_only=True, root=tmp_path)
    assert gate.admit([tmp_path / "never_existed.txt"]) == []
    assert gate.seen == {}


def test_snapshot_covers_every_source_through_the_parsers(tmp_path, monkeypatch):
    """The snapshot reuses plan_ingest, so it cannot drift from what parsers read."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha", encoding="utf-8")
    (docs / "b.txt").write_text("beta", encoding="utf-8")
    config = _text_config(tmp_path)
    monkeypatch.setattr(document_watch, "get_config", lambda: config)

    snapshot = document_watch._snapshot()

    assert set(snapshot) == {"docs/a.txt", "docs/b.txt"}


# --------------------------------------------------------------------------- #
# Settle delay
# --------------------------------------------------------------------------- #
def test_a_file_written_right_now_is_held_back(monkeypatch):
    """A large PDF is visible long before the copy finishes."""
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_SETTLE", 5)
    now = 1_000_000_000_000_000_000

    settled = document_watch._settled(
        {
            "fresh.pdf": f"100:{now - 1_000_000_000}",  # 1 second old
            "old.pdf": f"100:{now - 60_000_000_000}",  # 60 seconds old
        },
        now,
    )

    assert list(settled) == ["old.pdf"]


def test_a_malformed_token_is_not_allowed_to_stall_the_watcher(monkeypatch):
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_SETTLE", 5)
    settled = document_watch._settled({"weird.pdf": "not-a-token"}, 1_000)
    assert list(settled) == ["weird.pdf"]


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
# These call run_pass directly. An earlier version drove watch_documents and
# counted event-loop yields to decide when a pass had happened, which passed
# locally and failed on CI's slower runner: two of the tests never reached their
# second pass. Scheduling is not something a test should be betting on.
@pytest.fixture
def passes(monkeypatch):
    """Fixed snapshots and a recording ingest, so a pass is fully determined."""
    calls: list[dict[str, str]] = []
    snapshots: list[dict[str, str]] = []

    async def fake_ingest():
        calls.append({"ingested": 1})
        return {"ingested": 1, "pruned": 0}

    monkeypatch.setattr(document_watch, "_snapshot", lambda: snapshots.pop(0) if snapshots else {})
    monkeypatch.setattr(document_watch, "_ingest_now", fake_ingest)
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_SETTLE", 0)
    return calls, snapshots


def _pass(previous):
    return asyncio.run(document_watch.run_pass(previous))


def test_the_first_pass_only_learns_the_state(passes):
    """The startup ingest has already run, so acting on pass one would repeat it."""
    calls, snapshots = passes
    snapshots.append({"a.txt": "1:1"})

    baseline = _pass(None)

    assert baseline == {"a.txt": "1:1"}
    assert calls == [], "the first pass must never ingest"


def test_an_unchanged_folder_does_nothing(passes):
    calls, snapshots = passes
    snapshots.append({"a.txt": "1:1"})

    result = _pass({"a.txt": "1:1"})

    assert result == {"a.txt": "1:1"}
    assert calls == []


def test_a_new_file_triggers_one_ingest(passes):
    calls, snapshots = passes
    snapshots.extend([{"a.txt": "1:1", "b.txt": "2:2"}, {"a.txt": "1:1", "b.txt": "2:2"}])

    result = _pass({"a.txt": "1:1"})

    assert len(calls) == 1
    assert result == {"a.txt": "1:1", "b.txt": "2:2"}


def test_a_deletion_triggers_an_ingest_too(passes):
    """The folder is the source of truth, so removals must be acted on as well."""
    calls, snapshots = passes
    snapshots.extend([{"a.txt": "1:1"}, {"a.txt": "1:1"}])

    result = _pass({"a.txt": "1:1", "b.txt": "2:2"})

    assert len(calls) == 1
    assert result == {"a.txt": "1:1"}


def test_an_edit_triggers_an_ingest(passes):
    calls, snapshots = passes
    snapshots.extend([{"a.txt": "9:9"}, {"a.txt": "9:9"}])

    _pass({"a.txt": "1:1"})

    assert len(calls) == 1


def test_the_baseline_is_re_read_after_indexing(passes):
    """A long ingest can outlast further edits; the baseline must reflect reality."""
    calls, snapshots = passes
    snapshots.extend([
        {"a.txt": "1:1", "b.txt": "2:2"},          # what triggered the ingest
        {"a.txt": "1:1", "b.txt": "2:2", "c.txt": "3:3"},  # arrived during it
    ])

    result = _pass({"a.txt": "1:1"})

    assert len(calls) == 1
    assert "c.txt" in result, "a file that appeared mid-ingest must be in the baseline"


# --------------------------------------------------------------------------- #
# The loop around it
# --------------------------------------------------------------------------- #
def test_a_failing_pass_does_not_kill_the_loop(tmp_path, monkeypatch):
    """A watcher that dies on one error stops noticing changes, silently."""
    docs = tmp_path / "docs"
    docs.mkdir()
    attempts: list[int] = []

    async def flaky(previous):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("qdrant briefly unreachable")
        return {}

    monkeypatch.setattr(document_watch, "run_pass", flaky)
    monkeypatch.setattr(document_watch, "_watch_dirs", lambda: [str(docs)])
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_INTERVAL", 30)

    async def main():
        task = asyncio.create_task(document_watch.watch_documents())
        await asyncio.sleep(0.5)
        for i in range(3):
            (docs / f"f{i}.pdf").write_bytes(b"%PDF")
            await asyncio.sleep(0.4)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())

    assert len(attempts) >= 2, "the loop must keep reacting after an error"


# --------------------------------------------------------------------------- #
# What the UI is told
# --------------------------------------------------------------------------- #
# Chainlit has no toast API, and a background task has no session to push to, so
# the browser polls /ingest-status for this state instead.
def test_status_starts_idle_and_says_nothing():
    status = document_watch.get_status()
    assert status["state"] in {"idle", "done", "working", "error"}
    assert "revision" in status


def test_status_goes_working_then_done(passes):
    calls, snapshots = passes
    snapshots.extend([{"a.txt": "1:1", "b.txt": "2:2"}, {"a.txt": "1:1", "b.txt": "2:2"}])
    seen: list[tuple[str, str]] = []

    async def recording_ingest():
        seen.append((document_watch.get_status()["state"], document_watch.get_status()["message"]))
        return {"ingested": 3, "pruned": 0}

    document_watch._ingest_now = recording_ingest
    _pass({"a.txt": "1:1"})

    assert seen and seen[0][0] == "working", "must report working while it runs"
    assert "new document" in seen[0][1]
    final = document_watch.get_status()
    assert final["state"] == "done"
    assert "3 passages indexed" in final["message"]


def test_removal_is_named_explicitly(passes):
    """Deleting is what people worry about, so it must not hide behind 'indexing'."""
    calls, snapshots = passes
    snapshots.extend([{"a.txt": "1:1"}, {"a.txt": "1:1"}])
    seen: list[str] = []

    async def recording_ingest():
        seen.append(document_watch.get_status()["message"])
        return {"ingested": 0, "pruned": 7}

    document_watch._ingest_now = recording_ingest
    _pass({"a.txt": "1:1", "b.txt": "2:2"})

    assert seen and "removing 1 document" in seen[0]
    assert "7 passages removed" in document_watch.get_status()["message"]


def test_a_failed_run_is_reported_as_an_error(passes):
    calls, snapshots = passes
    snapshots.append({"a.txt": "9:9"})

    async def failing_ingest():
        raise RuntimeError("gateway down")

    document_watch._ingest_now = failing_ingest
    with pytest.raises(RuntimeError):
        _pass({"a.txt": "1:1"})

    status = document_watch.get_status()
    assert status["state"] == "error"
    assert "failed" in status["message"].lower()


def test_the_revision_advances_so_repeats_still_show(passes):
    """Two identical runs must look different to the browser, or the second is silent."""
    calls, snapshots = passes
    snapshots.extend([{"a.txt": "2:2"}, {"a.txt": "2:2"}, {"a.txt": "3:3"}, {"a.txt": "3:3"}])

    async def ingest():
        return {"ingested": 1, "pruned": 0}

    document_watch._ingest_now = ingest
    _pass({"a.txt": "1:1"})
    first = document_watch.get_status()
    _pass({"a.txt": "2:2"})
    second = document_watch.get_status()

    assert second["revision"] > first["revision"]
    assert second["message"] == first["message"], "same text, so only revision separates them"


def test_an_unchanged_folder_leaves_the_status_alone(passes):
    calls, snapshots = passes
    snapshots.append({"a.txt": "1:1"})
    before = document_watch.get_status()

    _pass({"a.txt": "1:1"})

    assert document_watch.get_status()["revision"] == before["revision"]


def test_the_status_names_the_files_for_the_hover_panel(passes):
    """A count alone is useless ("1 new document"); the panel needs names."""
    calls, snapshots = passes
    snapshots.extend([
        {"docs/a.pdf": "1:1", "docs/new_one.pdf": "2:2"},
        {"docs/a.pdf": "1:1", "docs/new_one.pdf": "2:2"},
    ])
    seen: list[list[dict]] = []

    async def recording_ingest():
        seen.append(document_watch.get_status().get("files"))
        return {"ingested": 2, "pruned": 0}

    document_watch._ingest_now = recording_ingest
    _pass({"docs/a.pdf": "1:1", "docs/gone.pdf": "9:9"})

    assert seen, "status must be published before the work starts"
    names = {f["name"]: f["action"] for f in seen[0]}
    assert names == {"new_one.pdf": "new", "gone.pdf": "removed"}
    assert "docs/" not in "".join(names), "basenames only, paths are noise"
    # And the finished status keeps them, so the panel still has detail afterwards.
    assert {f["name"] for f in document_watch.get_status()["files"]} == set(names)


def test_a_bulk_import_does_not_produce_an_endless_list(passes):
    calls, snapshots = passes
    many = {f"docs/f{i}.pdf": "1:1" for i in range(40)}
    snapshots.extend([many, many])
    seen: list[list[dict]] = []

    async def recording_ingest():
        seen.append(document_watch.get_status().get("files"))
        return {"ingested": 40, "pruned": 0}

    document_watch._ingest_now = recording_ingest
    _pass({})

    listed = seen[0]
    assert len(listed) == document_watch._MAX_LISTED_FILES + 1
    assert listed[-1]["action"] == "more"
    assert "more" in listed[-1]["name"]


# --------------------------------------------------------------------------- #
# The loop reacts to filesystem events
# --------------------------------------------------------------------------- #
def test_creating_a_file_wakes_the_loop(tmp_path, monkeypatch):
    """Polling took up to DOCUMENT_WATCH_INTERVAL to notice anything. With events the
    pass must run well before the timeout tick could have fired."""
    import time

    docs = tmp_path / "docs"
    docs.mkdir()
    passes: list[float] = []

    async def fake_pass(previous):
        passes.append(time.monotonic())
        return {}

    monkeypatch.setattr(document_watch, "run_pass", fake_pass)
    monkeypatch.setattr(document_watch, "_watch_dirs", lambda: [str(docs)])
    monkeypatch.setattr(document_watch, "DOCUMENT_WATCH_INTERVAL", 30)  # tick far away

    async def main():
        task = asyncio.create_task(document_watch.watch_documents())
        await asyncio.sleep(0.5)          # let the watcher start
        (docs / "new.pdf").write_bytes(b"%PDF-1.4")
        for _ in range(40):               # wait up to 4s for a pass
            if passes:
                break
            await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())

    assert passes, "a created file must wake the watcher without waiting for the tick"


def test_no_folders_means_no_watcher(monkeypatch, capsys):
    monkeypatch.setattr(document_watch, "_watch_dirs", lambda: [])
    asyncio.run(document_watch.watch_documents())
    assert "no document folders" in capsys.readouterr().out
