"""Generic ingestion CLI.

Examples::

    python -m kb.ingest --dry-run                 # parse+chunk, print samples, no embed
    python -m kb.ingest --config examples/minimal/rag.config.yaml
    python -m kb.ingest --recreate                # rebuild the collection
    python -m kb.ingest --only faq handbook       # ingest specific sources
    python -m kb.ingest --skip-if-exists          # no-op if the collection exists
"""

from __future__ import annotations

import argparse
import asyncio
import os

from config import CONFIG_PATH_ENV, load_config


def _print_dry_run(per_source, chunks, limit: int) -> None:
    print("DRY RUN: parsed and chunked, nothing embedded or written.\n")
    total = 0
    for s in per_source:
        total += s["chunks"]
        print(
            f"  source '{s['name']}' [{s['format']} / {s['strategy']}]: "
            f"{s['sections']} sections -> {s['chunks']} chunks"
        )
    print(f"\n  TOTAL: {total} chunks across {len(per_source)} source(s)\n")
    print(f"First {min(limit, len(chunks))} chunk(s):\n" + "-" * 60)
    for chunk in chunks[:limit]:
        preview = " ".join(chunk.text.split())
        if len(preview) > 300:
            preview = preview[:300] + "…"
        print(f"\n  doc_id : {chunk.doc_id}")
        print(f"  text   : {preview}")
        print(f"  metadata: {chunk.metadata}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest configured data sources into the vector store.")
    ap.add_argument("--config", default=os.getenv(CONFIG_PATH_ENV), help="Path to a rag config YAML.")
    ap.add_argument("--recreate", action="store_true", help="Drop and rebuild the collection.")
    ap.add_argument("--only", nargs="*", help="Ingest only these data source names.")
    ap.add_argument(
        "--skip-if-exists",
        action="store_true",
        help="Exit successfully if the collection already exists (ignored with --recreate). "
        "NOTE: this only checks existence. After changing the config's content or embed "
        "model you must use --recreate (or a new collection).",
    )
    ap.add_argument("--dry-run", action="store_true", help="Parse and chunk only; print samples.")
    ap.add_argument("--limit", type=int, default=5, help="How many sample chunks --dry-run prints.")
    args = ap.parse_args()

    config = load_config(args.config)
    only = set(args.only) if args.only else None

    if args.dry_run:
        from kb.ingestion_pipeline import plan_ingest

        per_source, chunks = plan_ingest(config, only=only)
        _print_dry_run(per_source, chunks, args.limit)
        return

    from kb.ingestion_pipeline import collection_exists, get_client, ingest_all

    if args.skip_if_exists and not args.recreate:
        if collection_exists(get_client(), config.vector_store.collection):
            print(f"Collection '{config.vector_store.collection}' exists; skipping ingestion.")
            return

    result = asyncio.run(ingest_all(config, recreate=args.recreate, only=only))
    print(f"Ingested {result['ingested']} chunks into '{result['collection']}'.")


if __name__ == "__main__":
    main()
