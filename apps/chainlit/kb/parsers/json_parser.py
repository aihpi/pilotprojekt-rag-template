"""Declarative JSON parser.

Two modes, driven by ``field_mapping``:

* ``record_specs`` — nested iteration with ancestor binding (for hierarchical
  JSON with irregular nesting).
* ``record_path`` / top-level list — one section per record (flat JSON).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from kb.parsers.base import Section, iter_source_files
from kb.parsers import register_parser
from kb.parsers._mapping import (
    ConfigMappingError,
    sections_from_record_specs,
    sections_from_records,
    _records_from_flat,
)

if TYPE_CHECKING:
    from config.schema import DataSourceConfig, RagConfig


@register_parser("json")
def parse_json(source: "DataSourceConfig", config: "RagConfig") -> list[Section]:
    mapping = source.field_mapping
    if mapping is None:  # guarded by schema validation, but be explicit
        raise ConfigMappingError(f"data source '{source.name}': json format requires field_mapping.")

    base = config.resolve_path(source.path)
    sections: list[Section] = []
    for path in iter_source_files(base, source.glob, "*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if mapping.record_specs:
            sections.extend(sections_from_record_specs(data, mapping, source.name))
        else:
            records = _records_from_flat(data, mapping, source.name)
            sections.extend(sections_from_records(records, mapping, source.name))
    return sections
