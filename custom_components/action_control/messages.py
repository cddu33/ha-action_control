"""User-facing texts for notifications and sensor attributes.

Notification bodies are built at runtime, so they can't come from
strings.json; they are picked from hass.config.language, English by default.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

DEFAULT_LANGUAGE = "en"

_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "failure": "{entity_id} did not reach the requested state/attributes.",
        "escalated": "A recovery action was triggered and the command replayed.",
        "mismatch_line": "- {attribute}: expected {expected}, actual {actual}",
        "mismatch": "{attribute}: expected {expected}, actual {actual}",
        "different_from": "different from {baseline}",
    },
    "fr": {
        "failure": "{entity_id} n'a pas atteint l'état/les attributs demandés.",
        "escalated": "Une action de secours a été déclenchée et la commande rejouée.",
        "mismatch_line": "- {attribute} : attendu {expected}, actuel {actual}",
        "mismatch": "{attribute} : attendu {expected}, actuel {actual}",
        "different_from": "différent de {baseline}",
    },
}


def texts_for(hass: HomeAssistant) -> dict[str, str]:
    """Return the message templates for Home Assistant's configured language."""
    language = (hass.config.language or DEFAULT_LANGUAGE).split("-")[0].lower()
    return _TEXTS.get(language, _TEXTS[DEFAULT_LANGUAGE])


def render(texts: dict[str, str], key: str, **kwargs: Any) -> str:
    """Render one template, falling back to English if a key is missing."""
    template = texts.get(key) or _TEXTS[DEFAULT_LANGUAGE][key]
    return template.format(**kwargs)
