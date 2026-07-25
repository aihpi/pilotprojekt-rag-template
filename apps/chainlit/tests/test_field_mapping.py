"""Unit tests for the JSON/CSV field-mapping DSL.

Covers: flat array, nested iteration with ancestor binding, missing path,
wrong type, and empty arrays.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.schema import FieldMapping, IterStep, RecordSpec
from kb.parsers._mapping import (
    ConfigMappingError,
    _records_from_flat,
    sections_from_record_specs,
    sections_from_records,
)



# --------------------------------------------------------------------------- #
# 1. Flat array
# --------------------------------------------------------------------------- #
def test_flat_array_top_level_list():
    data = [
        {"question": "What is X?", "answer": "X is a thing.", "cat": "faq"},
        {"question": "What is Y?", "answer": "Y is another.", "cat": "faq"},
    ]
    mapping = FieldMapping(
        text_template="Q: {question}\n\nA: {answer}",
        metadata={"title": "question", "category": "cat"},
    )
    records = _records_from_flat(data, mapping, "faq")
    sections = sections_from_records(records, mapping, "faq")
    assert len(sections) == 2
    assert sections[0].text == "Q: What is X?\n\nA: X is a thing."
    assert sections[0].metadata["title"] == "What is X?"
    assert sections[0].metadata["category"] == "faq"
    assert sections[0].metadata["source"] == "faq"


def test_flat_array_via_record_path():
    data = {"result": {"items": [{"t": "a"}, {"t": "b"}, {"t": "c"}]}}
    mapping = FieldMapping(record_path="result.items", text_fields=["t"])
    records = _records_from_flat(data, mapping, "src")
    assert len(records) == 3
    sections = sections_from_records(records, mapping, "src")
    assert [s.text for s in sections] == ["a", "b", "c"]


# --------------------------------------------------------------------------- #
# 2. Nested iteration with ancestor binding + bind_key_as
# --------------------------------------------------------------------------- #
def test_nested_iteration_with_binding():
    data = {
        "layers": [
            {
                "id": "L1",
                "modules": [
                    {
                        "id": "M1",
                        "reqs": {
                            "basic": [{"id": "R1", "body": "do a"}],
                            "high": [{"id": "R2", "body": "do b"}],
                        },
                    }
                ],
            }
        ]
    }
    mapping = FieldMapping(
        record_specs=[
            RecordSpec(
                iterate=[
                    IterStep(path="layers", as_="layer"),
                    IterStep(path="modules", as_="module"),
                    IterStep(path="reqs", object=True),
                    IterStep(path=["basic", "high"], as_="req", bind_key_as="level"),
                ],
                text_template="{req.id}: {req.body}",
                metadata={
                    "layer_id": "layer.id",
                    "module_id": "module.id",
                    "level": "@level",
                    "req_id": "req.id",
                },
            )
        ]
    )
    sections = sections_from_record_specs(data, mapping, "reqs")
    assert len(sections) == 2
    by_req = {s.metadata["req_id"]: s for s in sections}
    assert by_req["R1"].text == "R1: do a"
    assert by_req["R1"].metadata == {
        "source": "reqs",
        "layer_id": "L1",
        "module_id": "M1",
        "level": "basic",
        "req_id": "R1",
    }
    assert by_req["R2"].metadata["level"] == "high"


# --------------------------------------------------------------------------- #
# 3. Missing path -> rich ConfigMappingError
# --------------------------------------------------------------------------- #
def test_missing_record_path_raises_rich_error():
    data = {"result": {"rows": []}}
    mapping = FieldMapping(record_path="result.items", text_fields=["t"])
    with pytest.raises(ConfigMappingError) as exc:
        _records_from_flat(data, mapping, "src")
    msg = str(exc.value)
    assert "result.items" in msg          # full path
    assert "items" in msg                 # the missing key
    assert "Correct syntax" in msg        # a syntax example
    assert "src" in msg                   # source name


def test_missing_iterate_step_path_raises():
    data = {"layers": [{"id": "L1"}]}  # no 'modules'
    mapping = FieldMapping(
        record_specs=[
            RecordSpec(
                iterate=[IterStep(path="layers", as_="layer"), IterStep(path="modules", as_="m")],
                text_template="{m.id}",
            )
        ]
    )
    with pytest.raises(ConfigMappingError) as exc:
        sections_from_record_specs(data, mapping, "reqs")
    msg = str(exc.value)
    assert "modules" in msg and "Correct syntax" in msg


# --------------------------------------------------------------------------- #
# 4. Wrong type
# --------------------------------------------------------------------------- #
def test_wrong_type_record_path():
    data = {"items": "not-a-list"}
    mapping = FieldMapping(record_path="items", text_fields=["t"])
    with pytest.raises(ConfigMappingError) as exc:
        _records_from_flat(data, mapping, "src")
    msg = str(exc.value)
    assert "list" in msg and "str" in msg


def test_wrong_type_iterate_step():
    data = {"layers": {"not": "a list"}}  # 'layers' should be a list to iterate
    mapping = FieldMapping(
        record_specs=[
            RecordSpec(iterate=[IterStep(path="layers", as_="layer")], text_template="{layer}")
        ]
    )
    with pytest.raises(ConfigMappingError) as exc:
        sections_from_record_specs(data, mapping, "src")
    assert "a list to iterate" in str(exc.value)


# --------------------------------------------------------------------------- #
# 5. Empty array -> no sections, no error
# --------------------------------------------------------------------------- #
def test_empty_array_yields_no_sections():
    mapping = FieldMapping(record_path="items", text_fields=["t"])
    records = _records_from_flat({"items": []}, mapping, "src")
    assert records == []
    assert sections_from_records(records, mapping, "src") == []


def test_missing_sibling_level_is_skipped_not_error():
    # 'high' present, 'basic' absent -> the absent sibling key is skipped.
    data = {"reqs": {"high": [{"id": "R2", "body": "b"}]}}
    mapping = FieldMapping(
        record_specs=[
            RecordSpec(
                iterate=[
                    IterStep(path="reqs", object=True),
                    IterStep(path=["basic", "high"], as_="req", bind_key_as="level"),
                ],
                text_template="{req.id}",
                metadata={"level": "@level"},
            )
        ]
    )
    sections = sections_from_record_specs(data, mapping, "src")
    assert len(sections) == 1 and sections[0].metadata["level"] == "high"
