"""Declarative CSV parser: one section per row via field-mapping."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

from kb.parsers.base import Section, iter_source_files
from kb.parsers import register_parser
from kb.parsers._mapping import ConfigMappingError, sections_from_records

if TYPE_CHECKING:
    from config.schema import DataSourceConfig, RagConfig


@register_parser("csv")
def parse_csv(source: "DataSourceConfig", config: "RagConfig") -> list[Section]:
    mapping = source.field_mapping
    if mapping is None:
        raise ConfigMappingError(f"data source '{source.name}': csv format requires field_mapping.")

    base = config.resolve_path(source.path)
    sections: list[Section] = []
    for path in iter_source_files(base, source.glob, "*.csv"):
        # utf-8-sig transparently strips a BOM if present.
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh, delimiter=mapping.delimiter)
            rows = list(reader)
        sections.extend(sections_from_records(rows, mapping, source.name))
    return sections
