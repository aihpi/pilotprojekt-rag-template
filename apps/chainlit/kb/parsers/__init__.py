"""Parser registry.

Register a parser with :func:`register_parser` keyed by ``format`` name (or a
custom ``parser_name``). New corpora usually only need the built-in ``json`` /
``csv`` field-mapping parsers; irreducibly domain-specific structure gets a
``custom`` parser (see :mod:`kb.parsers.example_custom`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kb.parsers.base import ParserFn, Section, iter_source_files

if TYPE_CHECKING:
    from config.schema import DataSourceConfig

PARSER_REGISTRY: dict[str, ParserFn] = {}


def register_parser(*names: str):
    def deco(fn: ParserFn) -> ParserFn:
        for name in names:
            PARSER_REGISTRY[name] = fn
        return fn

    return deco


def get_parser(source: "DataSourceConfig") -> ParserFn:
    key = source.parser_name if source.format == "custom" else source.format
    parser = PARSER_REGISTRY.get(key)
    if parser is None:
        raise KeyError(
            f"data source '{source.name}': no parser registered for "
            f"'{key}'. Registered: {sorted(PARSER_REGISTRY)}"
        )
    return parser


# Import built-in parsers to populate the registry. Keep these light: no heavy
# third-party imports at module load (Docling is imported lazily in pdf.py).
# The json/csv field-mapping parsers are added in a later step.
from kb.parsers import example_custom, pdf, text  # noqa: E402,F401

try:  # optional until the field-mapping DSL lands
    from kb.parsers import csv_parser, json_parser  # noqa: E402,F401
except ImportError:
    pass

__all__ = [
    "PARSER_REGISTRY",
    "register_parser",
    "get_parser",
    "Section",
    "iter_source_files",
]
