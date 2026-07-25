"""Generic ingestion pipeline: parse -> chunk -> embed -> upsert.

``plan_ingest`` is the pure (no-I/O) parse+chunk stage — used by ``--dry-run``
and importable without Qdrant/LiteLLM. ``ingest_chunks``/``ingest_all`` add
embedding and Qdrant upserts, with config-driven payload indexes and an
embed-model sentinel guard. Heavy imports (qdrant_client, llm.embed) are
deferred into the functions that need them.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Awaitable, Callable

from config import RagConfig, get_config
from kb.chunkers import get_chunker
from kb.chunkers.base import Chunk
from kb.parsers import get_parser

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], Awaitable[None]]

# Deterministic id for the per-collection metadata sentinel point. It carries
# no "text" payload, so retrieval (which drops text-less payloads) never
# surfaces it as a result.
_SENTINEL_KEY = "__ingest_meta__"


def _point_id(doc_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id))


# --------------------------------------------------------------------------- #
# Pure parse + chunk (no embedding, no Qdrant)
# --------------------------------------------------------------------------- #
def plan_ingest(
    config: RagConfig | None = None,
    *,
    only: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[Chunk]]:
    """Parse and chunk every (selected) data source. Returns (per-source
    summary, all chunks). No I/O beyond reading source files."""
    config = config or get_config()
    per_source: list[dict[str, Any]] = []
    all_chunks: list[Chunk] = []
    for src in config.data_sources:
        if only and src.name not in only:
            continue
        parser = get_parser(src)
        chunking = src.chunking or config.chunking
        chunker = get_chunker(chunking.strategy)
        sections = parser(src, config)
        for section in sections:
            if src.extra_metadata:
                section.metadata.update(src.extra_metadata)
        chunks = chunker(sections, chunking)
        for i, chunk in enumerate(chunks):
            if not chunk.doc_id:
                chunk.doc_id = f"{src.name}:{i}"
        per_source.append(
            {
                "name": src.name,
                "format": src.format,
                "strategy": chunking.strategy,
                "sections": len(sections),
                "chunks": len(chunks),
            }
        )
        all_chunks.extend(chunks)
    return per_source, all_chunks


# --------------------------------------------------------------------------- #
# Qdrant helpers (lazy heavy imports)
# --------------------------------------------------------------------------- #
def get_client():
    from qdrant_client import QdrantClient

    from settings import QDRANT_API_KEY, QDRANT_URL

    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def collection_exists(client, name: str) -> bool:
    return name in {c.name for c in client.get_collections().collections}


def _distance(name: str):
    from qdrant_client.models import Distance

    return {"cosine": Distance.COSINE, "dot": Distance.DOT, "euclid": Distance.EUCLID}[name]


def _ensure_collection(client, name: str, size: int, distance: str, recreate: bool) -> None:
    from qdrant_client.models import VectorParams

    if recreate and collection_exists(client, name):
        client.delete_collection(collection_name=name, timeout=60)
    if not collection_exists(client, name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=size, distance=_distance(distance)),
        )


def _ensure_payload_indexes(client, name: str, fields: list[str]) -> None:
    from qdrant_client.models import PayloadSchemaType

    for field in fields:
        try:
            client.create_payload_index(
                collection_name=name, field_name=field, field_schema=PayloadSchemaType.KEYWORD
            )
        except Exception:  # noqa: BLE001 — index may already exist
            pass


def _read_sentinel(client, name: str) -> dict[str, Any] | None:
    try:
        records = client.retrieve(
            collection_name=name, ids=[_point_id(_SENTINEL_KEY)], with_payload=True
        )
    except Exception:  # noqa: BLE001
        return None
    return dict(records[0].payload) if records else None


def _write_sentinel(client, name: str, embed_model: str, size: int, vector: list[float]) -> None:
    from qdrant_client.models import PointStruct

    client.upsert(
        collection_name=name,
        points=[
            PointStruct(
                id=_point_id(_SENTINEL_KEY),
                vector=vector,
                payload={"_meta": True, "embed_model": embed_model, "vector_size": size},
            )
        ],
    )


# --------------------------------------------------------------------------- #
# Embed + upsert
# --------------------------------------------------------------------------- #
def _payload(chunk: Chunk) -> dict[str, Any]:
    return {"text": chunk.text, **chunk.metadata}


async def ingest_chunks(
    chunks: list[Chunk],
    *,
    collection: str,
    distance: str = "cosine",
    payload_indexes: list[str] | None = None,
    recreate: bool = False,
    batch_size: int | None = None,
    max_batch_chars: int | None = None,
    embed_model: str | None = None,
    progress: ProgressCallback | None = None,
) -> int:
    from qdrant_client.models import PointStruct

    from llm import embed

    if not chunks:
        logger.info("ingest: no chunks to ingest")
        return 0

    batch_size = batch_size or int(os.getenv("INGEST_BATCH_SIZE", "64"))
    max_batch_chars = max_batch_chars or int(os.getenv("INGEST_MAX_BATCH_CHARS", "20000"))
    embed_model = embed_model or get_config().models.embed_model
    client = get_client()

    # Sentinel guard: refuse a silent embed-model swap into an existing collection.
    if collection_exists(client, collection) and not recreate:
        sentinel = _read_sentinel(client, collection)
        prev = sentinel.get("embed_model") if sentinel else None
        if prev and prev != embed_model:
            raise RuntimeError(
                f"Collection '{collection}' was built with embed model '{prev}', but the "
                f"config uses '{embed_model}'. Vectors would be incompatible. Re-ingest with "
                f"--recreate, or set vector_store.collection to a new name."
            )

    first_vec = (await embed([chunks[0].text]))[0]
    _ensure_collection(client, collection, len(first_vec), distance, recreate)
    _ensure_payload_indexes(client, collection, payload_indexes or [])
    _write_sentinel(client, collection, embed_model, len(first_vec), first_vec)

    client.upsert(
        collection_name=collection,
        points=[PointStruct(id=_point_id(chunks[0].doc_id), vector=first_vec, payload=_payload(chunks[0]))],
    )

    start = 1
    current = batch_size
    while start < len(chunks):
        batch: list[Chunk] = []
        total = 0
        for chunk in chunks[start : start + current]:
            n = len(chunk.text)
            if batch and total + n > max_batch_chars:
                break
            batch.append(chunk)
            total += n
        vectors = await embed([c.text for c in batch])
        points = [
            PointStruct(id=_point_id(c.doc_id), vector=v, payload=_payload(c))
            for c, v in zip(batch, vectors, strict=True)
        ]
        try:
            client.upsert(collection_name=collection, points=points)
            start += len(batch)
            if progress:
                await progress(min(start, len(chunks)), len(chunks))
        except Exception as exc:  # noqa: BLE001
            if "Payload error" in str(exc) and current > 1:
                current = max(1, current // 2)
                logger.warning("ingest: batch too large, halving to %d and retrying", current)
                continue
            raise

    logger.info("ingest: upserted %d chunks into '%s'", len(chunks), collection)
    return len(chunks)


async def ingest_all(
    config: RagConfig | None = None,
    *,
    recreate: bool = False,
    only: set[str] | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    config = config or get_config()
    per_source, all_chunks = plan_ingest(config, only=only)
    count = await ingest_chunks(
        all_chunks,
        collection=config.vector_store.collection,
        distance=config.vector_store.distance,
        payload_indexes=config.retrieval.payload_indexes,
        recreate=recreate,
        embed_model=config.models.embed_model,
        progress=progress,
    )
    return {"ingested": count, "collection": config.vector_store.collection, "sources": per_source}


def drop_collection(name: str) -> None:
    client = get_client()
    if collection_exists(client, name):
        client.delete_collection(collection_name=name, timeout=60)
