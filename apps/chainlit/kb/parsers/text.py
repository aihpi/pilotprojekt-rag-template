"""Plain-text and Markdown parser: one section per file."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kb.parsers.base import Section, iter_source_files
from kb.parsers import register_parser

if TYPE_CHECKING:
    from config.schema import DataSourceConfig, RagConfig

_DEFAULT_GLOBS = {"txt": "*.txt", "md": "*.md"}


@register_parser("txt", "md")
def parse_text(source: "DataSourceConfig", config: "RagConfig") -> list[Section]:
    base = config.resolve_path(source.path)
    default_glob = _DEFAULT_GLOBS.get(source.format, "*.txt")
    sections: list[Section] = []
    for path in iter_source_files(base, source.glob, default_glob):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        sections.append(
            Section(
                text=text,
                doc_id=f"text:{path.name}",
                metadata={
                    "file": path.name,
                    "source": path.name,
                    "source_file": path.name,
                    "title": path.stem,
                    "section_title": path.stem,
                    "page_start": None,
                    "page_end": None,
                },
            )
        )
    return sections
