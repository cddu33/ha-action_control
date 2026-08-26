"""Guards on the translation files.

These caught nothing but manual review until now: a missing or renamed key
breaks nothing at runtime, it just renders as a raw field name in the UI,
so neither ruff, pytest nor hassfest notices.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "action_control"
STRINGS = COMPONENT / "strings.json"
TRANSLATIONS = COMPONENT / "translations"
LANGUAGES = ["en", "fr"]


def _flatten(data: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested keys into "a.b.c" paths."""
    flat: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def _load(language: str) -> dict[str, str]:
    return _flatten(json.loads((TRANSLATIONS / f"{language}.json").read_text()))


def test_strings_json_matches_english_translations_byte_for_byte():
    """strings.json and translations/en.json are kept identical on purpose."""
    assert STRINGS.read_text() == (TRANSLATIONS / "en.json").read_text()


def test_every_language_has_the_same_keys():
    reference = _load("en")
    for language in LANGUAGES:
        keys = set(_load(language))
        assert keys == set(reference), (
            f"{language}.json key mismatch: "
            f"missing {sorted(set(reference) - keys)}, extra {sorted(keys - set(reference))}"
        )


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_translation_is_empty(language: str):
    empty = [key for key, value in _load(language).items() if not str(value).strip()]
    assert not empty, f"{language}.json has empty values: {empty}"
