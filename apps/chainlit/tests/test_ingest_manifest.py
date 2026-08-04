"""Incremental ingest: the file gate and the per-collection file manifest.

Before this, the compose ``ingest`` service exited as soon as the target
collection existed, so a PDF dropped into the documents folder was never indexed
and nothing said so. A run is now incremental: files already indexed and
unchanged are skipped, new and edited ones are not.

No Qdrant, LiteLLM or Docling here — the pipeline talks to a fake client and a
fake embedder, and the sources are plain text files.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import kb.ingestion_pipeline as pipeline
from config.schema import ChunkingConfig, DataSourceConfig, RagConfig
from kb.parsers.base import FileGate, file_gate, iter_source_files


def _config_at(dir_path: Path, **kw) -> RagConfig:
    cfg = RagConfig(**kw)
    cfg._config_dir = dir_path
    return cfg


def _text_config(tmp_path: Path) -> RagConfig:
    return _config_at(
        tmp_path,
        data_sources=[
            DataSourceConfig(name="docs", path="docs", format="txt", glob="*.txt"),
        ],
        chunking=ChunkingConfig(strategy="passthrough"),
    )


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def test_unchanged_file_is_skipped_and_still_recorded(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    gate = FileGate(root=tmp_path)

    first = gate.admit([target])
    assert first == [target]
    digest = gate.seen["a.txt"]

    # A second run that already knows this hash must not hand the file over.
    again = FileGate(known={"a.txt": digest}, root=tmp_path)
    assert again.admit([target]) == []
    # ...but the caller still learns the file exists, so the manifest keeps it.
    assert again.seen == {"a.txt": digest}
    assert again.skipped == ["a.txt"]


def test_edited_file_is_ingested_again(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("first version", encoding="utf-8")
    stale = FileGate(root=tmp_path)
    stale.admit([target])
    old_digest = stale.seen["a.txt"]

    target.write_text("second version", encoding="utf-8")
    gate = FileGate(known={"a.txt": old_digest}, root=tmp_path)

    assert gate.admit([target]) == [target], "an edited file must not be treated as indexed"
    assert gate.seen["a.txt"] != old_digest


def test_skip_all_enumerates_without_returning_anything(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    gate = FileGate(skip_all=True, root=tmp_path)

    admitted = gate.admit(sorted(tmp_path.glob("*.txt")))

    assert admitted == []
    assert set(gate.seen) == {"a.txt", "b.txt"}


def test_gate_keys_are_relative_to_the_config_dir(tmp_path):
    nested = tmp_path / "data" / "documents"
    nested.mkdir(parents=True)
    target = nested / "paper.pdf"
    target.write_bytes(b"%PDF-1.4")
    gate = FileGate(root=tmp_path)

    gate.admit([target])

    assert list(gate.seen) == ["data/documents/paper.pdf"]


def test_iter_source_files_applies_the_active_gate(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    gate = FileGate(skip_all=True, root=tmp_path)

    with file_gate(gate):
        assert iter_source_files(tmp_path, "*.txt", "*.txt") == []
    # Outside the context the helper behaves exactly as before.
    assert len(iter_source_files(tmp_path, "*.txt", "*.txt")) == 2


def test_docling_json_directory_is_gated_too(tmp_path):
    """pdf.py used json_dir.glob() directly, bypassing the gate entirely."""
    from kb.parsers import pdf as pdf_parser

    (tmp_path / "one.json").write_text("{}", encoding="utf-8")
    gate = FileGate(skip_all=True, root=tmp_path)

    with file_gate(gate):
        sections = pdf_parser._sections_from_docling_json(
            tmp_path, ChunkingConfig(strategy="passthrough"), True
        )

    assert sections == []
    assert list(gate.seen) == ["one.json"]


# --------------------------------------------------------------------------- #
# A fake Qdrant client, just enough for ingest_all
# --------------------------------------------------------------------------- #
class _Record:
    def __init__(self, payload=None, vector=None):
        self.payload = payload
        self.vector = vector


class FakeClient:
    def __init__(self, collections=()):
        self.points: dict[str, _Record] = {}
        self.collections = set(collections)
        self.upserts: list[list] = []

    def get_collections(self):
        names = [type("C", (), {"name": n})() for n in self.collections]
        return type("R", (), {"collections": names})()

    def retrieve(self, collection_name, ids, with_payload=False, with_vectors=False):
        return [self.points[i] for i in ids if i in self.points]

    def scroll(self, collection_name, limit=1000, offset=None, **kw):
        payloads = [
            _Record(payload=r.payload) for r in self.points.values() if r.payload
        ]
        return payloads, None

    def upsert(self, collection_name, points):
        self.upserts.append(points)
        for point in points:
            self.points[point.id] = _Record(payload=point.payload, vector=point.vector)

    def create_collection(self, collection_name, vectors_config=None):
        self.collections.add(collection_name)

    def delete_collection(self, collection_name, timeout=None):
        self.collections.discard(collection_name)
        self.points.clear()

    def create_payload_index(self, **kw):
        return None


@pytest.fixture
def fake_embed(monkeypatch):
    calls: list[list[str]] = []

    async def embed(texts):
        calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]

    import llm

    monkeypatch.setattr(llm, "embed", embed)
    return calls


def _run(config, client, monkeypatch, **kw):
    monkeypatch.setattr(pipeline, "get_client", lambda: client)
    return asyncio.run(pipeline.ingest_all(config, **kw))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def test_first_ingest_writes_a_manifest(tmp_path, monkeypatch, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")
    client = FakeClient()

    result = _run(_text_config(tmp_path), client, monkeypatch)

    assert result["ingested"] == 1
    stored = pipeline.read_manifest(client, "documents")
    assert list(stored) == ["docs/a.txt"]


def test_second_run_does_nothing(tmp_path, monkeypatch, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)

    _run(config, client, monkeypatch)
    fake_embed.clear()
    result = _run(config, client, monkeypatch)

    assert result["ingested"] == 0
    assert result["skipped"] == 1
    assert fake_embed == [], "an unchanged corpus must not be embedded again"


def test_a_new_file_is_picked_up_without_touching_the_rest(tmp_path, monkeypatch, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)
    _run(config, client, monkeypatch)

    # This is the reported bug: adding a document and running again did nothing.
    (docs / "b.txt").write_text("beta content", encoding="utf-8")
    fake_embed.clear()
    result = _run(config, client, monkeypatch)

    assert result["ingested"] == 1
    assert result["skipped"] == 1
    assert any("beta content" in t for batch in fake_embed for t in batch)
    assert not any("alpha content" in t for batch in fake_embed for t in batch)
    assert set(pipeline.read_manifest(client, "documents")) == {"docs/a.txt", "docs/b.txt"}


def test_recreate_ignores_the_manifest(tmp_path, monkeypatch, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")
    client = FakeClient()
    config = _text_config(tmp_path)
    _run(config, client, monkeypatch)

    fake_embed.clear()
    result = _run(config, client, monkeypatch, recreate=True)

    assert result["ingested"] == 1
    assert any("alpha content" in t for batch in fake_embed for t in batch)


def test_existing_collection_without_a_manifest_is_adopted(tmp_path, monkeypatch, fake_embed, capsys):
    """Every collection built by the old code hits this path.

    Treating it as empty would re-embed the whole corpus, and with
    images.mode: describe re-describe every figure, so adoption records the files
    and ingests nothing.
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha content", encoding="utf-8")

    client = FakeClient(collections={"documents"})
    # A pre-manifest collection: a sentinel and a chunk, but no manifest point.
    client.points[pipeline._point_id(pipeline._SENTINEL_KEY)] = _Record(
        payload={"_meta": True, "embed_model": "m"}, vector=[0.1, 0.2, 0.3]
    )
    client.points["chunk-1"] = _Record(payload={"source_file": "a.txt", "text": "alpha"})

    result = _run(_text_config(tmp_path), client, monkeypatch, recreate=False)

    assert result["ingested"] == 0
    assert result["adopted"] == 1
    assert fake_embed == [], "adoption must not embed anything"
    assert list(pipeline.read_manifest(client, "documents")) == ["docs/a.txt"]
    assert "adopting existing collection" in capsys.readouterr().out


def test_adoption_warns_about_files_that_have_no_chunks(tmp_path, monkeypatch, fake_embed, capsys):
    """A document the user believes is searchable but is not."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "indexed.txt").write_text("in the collection", encoding="utf-8")
    (docs / "missing.txt").write_text("never ingested", encoding="utf-8")

    client = FakeClient(collections={"documents"})
    client.points[pipeline._point_id(pipeline._SENTINEL_KEY)] = _Record(
        payload={"_meta": True, "embed_model": "m"}, vector=[0.1, 0.2, 0.3]
    )
    client.points["chunk-1"] = _Record(payload={"source_file": "indexed.txt", "text": "x"})

    _run(_text_config(tmp_path), client, monkeypatch)

    out = capsys.readouterr().out
    assert "docs/missing.txt" in out
    assert "docs/indexed.txt" not in out.split("WARNING")[-1]


def test_adoption_matches_the_pdf_naming_convention(tmp_path, monkeypatch, fake_embed, capsys):
    """The PDF parser stores '<stem>.pdf' as source_file, so a .json source
    indexes X.pdf. Adoption must not report such a file as missing."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "paper.json").write_text("{}", encoding="utf-8")

    client = FakeClient(collections={"documents"})
    client.points[pipeline._point_id(pipeline._SENTINEL_KEY)] = _Record(
        payload={"_meta": True, "embed_model": "m"}, vector=[0.1, 0.2, 0.3]
    )
    client.points["chunk-1"] = _Record(payload={"source_file": "paper.pdf", "text": "x"})

    config = _config_at(
        tmp_path,
        data_sources=[
            DataSourceConfig(name="docs", path="docs", format="txt", glob="*.json"),
        ],
        chunking=ChunkingConfig(strategy="passthrough"),
    )
    _run(config, client, monkeypatch)

    assert "WARNING" not in capsys.readouterr().out
