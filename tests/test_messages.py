"""Tests for the bilingual runtime notification templates."""
from __future__ import annotations

from custom_components.action_control import messages


async def test_texts_for_defaults_to_english(hass):
    hass.config.language = "en"
    texts = messages.texts_for(hass)
    assert texts is messages._TEXTS["en"]


async def test_texts_for_french(hass):
    hass.config.language = "fr"
    texts = messages.texts_for(hass)
    assert texts is messages._TEXTS["fr"]


async def test_texts_for_unknown_language_falls_back_to_english(hass):
    hass.config.language = "de"
    texts = messages.texts_for(hass)
    assert texts is messages._TEXTS["en"]


async def test_render_french_failure_and_escalated(hass):
    hass.config.language = "fr"
    texts = messages.texts_for(hass)
    assert (
        messages.render(texts, "failure", entity_id="light.kitchen")
        == "light.kitchen n'a pas atteint l'état/les attributs demandés."
    )
    assert (
        messages.render(texts, "escalated")
        == "Une action de secours a été déclenchée et la commande rejouée."
    )


async def test_render_french_mismatch_line_and_different_from(hass):
    hass.config.language = "fr"
    texts = messages.texts_for(hass)
    assert (
        messages.render(
            texts, "mismatch_line", attribute="brightness", expected=200, actual=100
        )
        == "- brightness : attendu 200, actuel 100"
    )
    assert (
        messages.render(texts, "different_from", baseline=0) == "différent de 0"
    )
