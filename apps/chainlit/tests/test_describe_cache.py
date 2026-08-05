"""Figure descriptions stored as per-paper markdown beside the figures.

A description costs a vision call, so re-reading a corpus must not pay twice.
These pin the parts that are easy to get wrong: that a failed description is
never stored (which would make the figure permanently undescribed), that the key
really covers image, prompt and model, and that a mangled file degrades to a
fresh call instead of raising.
"""

from __future__ import annotations

import kb.figure_store as figure_store
import pytest


@pytest.fixture
def figures(monkeypatch, tmp_path):
    """A figure dir under tmp_path, with the live vision call counted."""
    calls: list[tuple[str, str, str]] = []
    result = {"text": "a bar chart"}

    def fake_sync(uri, prompt, model):
        calls.append((uri, prompt, model))
        return result["text"]

    monkeypatch.setattr("llm.describe_image_sync", fake_sync)
    monkeypatch.setattr(figure_store, "_DESCRIBE_STORE_WARNED", False)
    return calls, result, tmp_path / "descriptions"


def describe(root, uri="data:image/jpeg;base64,AAA", prompt="describe", model="gemma", idx=0):
    return figure_store.describe_figure(
        uri, prompt, model, descriptions=root, stem="Paper_2024", fig_idx=idx
    )


def test_an_identical_second_call_reads_the_markdown(figures):
    calls, _result, root = figures
    assert describe(root) == "a bar chart"
    assert describe(root) == "a bar chart"
    assert len(calls) == 1, "the second call should have been served from disk"


def test_it_lands_in_a_per_paper_folder_as_readable_prose(figures):
    _calls, _result, root = figures
    describe(root, idx=3)
    path = root / "Paper_2024" / "fig3.md"
    assert path.is_file()
    text = path.read_text("utf-8")
    assert text.endswith("a bar chart\n"), "the description must be the readable body"
    assert text.startswith("---\nkey: "), "front matter carries the staleness key"
    assert list(path.parent.glob("*.tmp")) == [], "temp files must not be left behind"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"uri": "data:image/jpeg;base64,BBB"},  # figure changed
        {"prompt": "describe it fully"},  # prompt changed
        {"model": "qwen-vl"},  # model changed
    ],
)
def test_changing_any_input_regenerates(figures, kwargs):
    """describe_image_max_px is covered too: it changes the encoded bytes, so it
    changes the URI and therefore the key."""
    calls, _result, root = figures
    describe(root)
    describe(root, **kwargs)
    assert len(calls) == 2


def test_a_failed_description_is_not_stored(figures):
    """An empty result means the call failed. Storing it would leave the figure
    permanently undescribed and break the advice to re-ingest."""
    calls, result, root = figures
    result["text"] = ""
    assert describe(root) == ""
    assert not (root / "Paper_2024").exists()

    result["text"] = "recovered"
    assert describe(root) == "recovered"
    assert describe(root) == "recovered"
    assert len(calls) == 2, "the retry is stored, so the third call is a hit"


@pytest.mark.parametrize(
    "content",
    [
        "no front matter at all",
        "---\nkey: abc\n",  # truncated: no closing delimiter
        "---\nnot: [valid\n---\nbody",  # unreadable header
        "---\nkey: abc\n---\n",  # header does not match
        b"\xff\xfe torn write",  # not even UTF-8
    ],
)
def test_a_mangled_file_is_re_described_instead_of_raising(figures, content):
    calls, _result, root = figures
    describe(root)
    path = root / "Paper_2024" / "fig0.md"
    path.write_bytes(content) if isinstance(content, bytes) else path.write_text(content, "utf-8")

    assert describe(root) == "a bar chart"
    assert len(calls) == 2


def test_a_matching_header_with_an_empty_body_is_re_described(figures):
    """Separate from the mangled cases above, which all fail on the key and so never
    reach the body check. Here the key matches, so only the empty-body guard stands
    between the reader and "this figure has no description" forever."""
    calls, _result, root = figures
    describe(root)
    path = root / "Paper_2024" / "fig0.md"
    header = path.read_text("utf-8").split("---\n")[1]
    path.write_text(f"---\n{header}---\n   \n", "utf-8")  # real key, blank body

    assert describe(root) == "a bar chart"
    assert len(calls) == 2


def test_an_unwritable_store_still_returns_descriptions_and_warns_once(figures, monkeypatch, capsys):
    """Silence here would be expensive: every figure gets re-described on every run,
    at full vision-call cost, with nothing in the log to explain it."""
    calls, _result, root = figures

    def refuse(*_a, **_kw):
        raise PermissionError("read-only file system")

    monkeypatch.setattr("pathlib.Path.mkdir", refuse)
    for i in range(4):
        assert describe(root, uri=f"data:image/jpeg;base64,{i}") == "a bar chart"
    assert len(calls) == 4
    assert capsys.readouterr().out.count("cannot store figure descriptions") == 1


def test_a_crash_between_write_and_rename_leaves_no_temp_file(figures, monkeypatch):
    """The docstring sells this function on surviving an import that died partway,
    so its own litter must not accumulate in a folder users are told to browse."""
    _calls, _result, root = figures
    monkeypatch.setattr("os.replace", lambda *_a: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        describe(root)
    assert list((root / "Paper_2024").glob("*.tmp")) == []


def test_non_ascii_descriptions_round_trip(figures):
    """Descriptions are German in the shipped config, so the encoding matters."""
    calls, result, root = figures
    result["text"] = "Ein Säulendiagramm über Größenverhältnisse, 30 °C"
    assert describe(root) == result["text"]
    assert describe(root) == "Ein Säulendiagramm über Größenverhältnisse, 30 °C"
    assert len(calls) == 1
