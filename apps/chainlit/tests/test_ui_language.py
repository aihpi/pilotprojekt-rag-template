"""The interface speaks one language at a time, and both halves are complete.

Three front-end files carry their own German/English string table: the two badges
and the evaluation dashboard. They cannot share one — two are served by Chainlit
from ``public/``, the third by the eval container — so the tables are copies, and
the failure mode is a key added to one language and forgotten in the other. That
renders as the word ``undefined`` in the middle of a sentence, in the language
whoever added it does not read, which is exactly the kind of thing nobody notices.

Checked by counting definitions rather than by parsing JavaScript: a key that is
used must be defined twice, once per language. Crude, but it catches the whole of
the bug and needs no JS runtime in the test environment.

The resolution rule itself is tested through ``settings.starter_questions``, the
one place it is implemented in Python.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent

# (file, how the table is referenced in that file)
TABLES = [
    (APP / "public" / "eval-badge.js", "strings"),
    (APP / "public" / "ingest-status.js", "strings"),
    (APP / "eval_app" / "static" / "index.html", "S"),
]


@pytest.mark.parametrize("path, accessor", TABLES, ids=lambda v: getattr(v, "name", v))
def test_every_string_used_is_defined_in_both_languages(path, accessor):
    source = path.read_text(encoding="utf-8")
    used = set(re.findall(rf"\b{accessor}\.(\w+)", source))
    assert used, f"no {accessor}.* lookups found in {path.name} — did the table move?"

    for key in sorted(used):
        definitions = re.findall(rf"^\s*{key}:", source, re.M)
        assert len(definitions) == 2, (
            f"{path.name}: {accessor}.{key} is defined {len(definitions)}x, expected "
            "once under `de` and once under `en`"
        )


def test_every_surface_agrees_on_what_counts_as_german():
    """A split here would put one badge in German and its neighbour in English.

    The rule, not the surrounding code: the badges keep the resolved language in a
    variable so a re-render can depend on it, the dashboard resolves once at load,
    and the dashboard quotes its strings the other way round.
    """
    rule = re.compile(r"""indexOf\(['"]de['"]\) === 0""")
    for path, _ in TABLES:
        found = rule.findall(path.read_text(encoding="utf-8"))
        assert len(found) == 1, f"{path.name} does not resolve German the same way"


def test_the_badges_re_render_when_the_language_changes():
    """The evaluation badge skips a render when the payload is unchanged, so the
    language has to be part of that key — otherwise it would keep the previous
    language until a score happened to move."""
    source = (APP / "public" / "eval-badge.js").read_text(encoding="utf-8")
    assert "langCode + JSON.stringify(status)" in source


# --------------------------------------------------------------------------- #
# Starter questions: the one language-picking rule written in Python
# --------------------------------------------------------------------------- #


@pytest.fixture
def settings_for(monkeypatch, tmp_path):
    """Import ``settings`` against a config whose starter questions we choose."""

    def build(starters) -> object:
        import yaml

        from config import loader

        path = tmp_path / "rag.config.yaml"
        path.write_text(
            yaml.safe_dump({"prompt": {"starter_questions": starters}}), encoding="utf-8"
        )
        monkeypatch.setenv("RAG_CONFIG", str(path))
        loader.get_config.cache_clear()
        sys.modules.pop("settings", None)
        return importlib.import_module("settings")

    yield build
    sys.modules.pop("settings", None)


def test_a_plain_list_is_used_for_every_language(settings_for):
    # The common case, and the one every existing config is written in.
    settings = settings_for(["Was ist indexiert?"])
    assert settings.starter_questions("de-DE") == ["Was ist indexiert?"]
    assert settings.starter_questions("en-US") == ["Was ist indexiert?"]
    assert settings.starter_questions(None) == ["Was ist indexiert?"]


def test_a_mapping_is_picked_by_language(settings_for):
    settings = settings_for({"de": ["Frage"], "en": ["Question"]})
    assert settings.starter_questions("de-DE") == ["Frage"]
    assert settings.starter_questions("de-AT") == ["Frage"], "any de* is German"
    assert settings.starter_questions("en-US") == ["Question"]
    assert settings.starter_questions("fr-FR") == ["Question"], "not German means English"
    assert settings.starter_questions(None) == ["Question"]


def test_a_language_nobody_configured_still_shows_starters(settings_for):
    # Rather than an empty welcome screen, which reads as a broken instance.
    settings = settings_for({"de": ["Frage"]})
    assert settings.starter_questions("en-US") == ["Frage"]
