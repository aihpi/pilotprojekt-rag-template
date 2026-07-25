"""Worked example of a custom parser — copy this file to add your own format.

The built-in ``pdf``/``txt``/``md``/``json``/``csv`` parsers plus the declarative
field-mapping DSL cover most corpora. Write a custom parser when a format needs
real logic: skipping records, deriving titles, flattening nested structures.

This example reads a JSON Lines file (one JSON object per line) such as::

    {"id": "a1", "title": "Water quality 2024", "body": "...", "page": 3, "draft": false}

Enable it from a config with::

    data_sources:
      - name: notes
        path: ./data/notes.jsonl
        format: custom
        parser_name: example_jsonl
        chunking: {strategy: passthrough}   # one chunk per record

The contract is small: return a list of :class:`~kb.parsers.base.Section`. Every
metadata key you set lands in the Qdrant payload, and ``source_file``/``title``/
``page_start`` are what the citation layer reads — set them and citations work.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from kb.parsers import register_parser
from kb.parsers.base import Section

if TYPE_CHECKING:
    from config.schema import DataSourceConfig, RagConfig


@register_parser("example_jsonl")
def parse_example_jsonl(source: "DataSourceConfig", config: "RagConfig") -> list[Section]:
    path = config.resolve_path(source.path)
    if not path.is_file():
        print(f"[ingest] example_jsonl: {path} not found — skipping source '{source.name}'")
        return []

    sections: list[Section] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[ingest] example_jsonl: skipping malformed line {line_no}: {exc}")
            continue
        if record.get("draft"):
            continue  # custom logic: drafts never make it into the index

        title = str(record.get("title") or "").strip()
        body = str(record.get("body") or "").strip()
        if not body:
            continue
        page = record.get("page")
        sections.append(
            Section(
                # Prefixing the title keeps it in the embedded text, which helps retrieval.
                text=f"{title}\n\n{body}".strip(),
                doc_id=f"{source.name}:{record.get('id') or line_no}",
                metadata={
                    "source_file": path.name,
                    "title": title or path.stem,
                    "section_title": title or path.stem,
                    "section_index": line_no,
                    "page_start": page if isinstance(page, int) else None,
                    "page_end": page if isinstance(page, int) else None,
                },
            )
        )
    return sections
