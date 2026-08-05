"""Parser + chunker integration tests (no Qdrant/LiteLLM/Docling required)."""

from __future__ import annotations

import json
from pathlib import Path

from config.schema import (
    DataSourceConfig,
    FieldMapping,
    RagConfig,
)
from kb.parsers import get_parser



def _config_at(dir_path: Path, **kw) -> RagConfig:
    cfg = RagConfig(**kw)
    cfg._config_dir = dir_path
    return cfg


# --------------------------------------------------------------------------- #
# CSV parser (BOM + delimiter)
# --------------------------------------------------------------------------- #
def test_csv_parser_handles_bom_and_delimiter(tmp_path):
    csv_path = tmp_path / "faq.csv"
    csv_path.write_text("question;answer\nWhat?;Because.\nWhen?;Now.\n", encoding="utf-8-sig")
    src = DataSourceConfig(
        name="faq",
        path="faq.csv",
        format="csv",
        field_mapping=FieldMapping(
            delimiter=";",
            text_template="Q: {question}\nA: {answer}",
            metadata={"title": "question"},
        ),
    )
    cfg = _config_at(tmp_path, data_sources=[src])
    sections = get_parser(src)(src, cfg)
    assert len(sections) == 2
    # BOM must be stripped from the first header, so {question} resolves.
    assert sections[0].text == "Q: What?\nA: Because."
    assert sections[0].metadata["title"] == "What?"


# --------------------------------------------------------------------------- #
# JSON parser (nested) through the registered parser
# --------------------------------------------------------------------------- #
def test_json_parser_nested_records(tmp_path):
    (tmp_path / "d.json").write_text(
        json.dumps({"groups": [{"g": "G1", "items": [{"id": 1}, {"id": 2}]}]}),
        encoding="utf-8",
    )
    from config.schema import IterStep, RecordSpec

    src = DataSourceConfig(
        name="d",
        path="d.json",
        format="json",
        field_mapping=FieldMapping(
            record_specs=[
                RecordSpec(
                    iterate=[IterStep(path="groups", as_="g"), IterStep(path="items", as_="it")],
                    text_template="item {it.id} in {g.g}",
                    metadata={"group": "g.g"},
                )
            ]
        ),
    )
    cfg = _config_at(tmp_path, data_sources=[src])
    sections = get_parser(src)(src, cfg)
    assert [s.text for s in sections] == ["item 1 in G1", "item 2 in G1"]
    assert all(s.metadata["group"] == "G1" for s in sections)
