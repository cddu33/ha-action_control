"""Tests for the per-rule status sensor."""
from __future__ import annotations

from homeassistant.core import ServiceCall
from homeassistant.helpers import entity_registry as er

from custom_components.action_control.const import DATA_ENGINE, DOMAIN


async def test_sensor_follows_the_rule_status(hass, mock_config_entry):
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    engine = hass.data[DOMAIN][mock_config_entry.entry_id][DATA_ENGINE]
    rule_id = next(iter(engine.rules))
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_{rule_id}"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state.state == "idle"
    assert set(state.attributes["options"]) == {
        "idle",
        "ok",
        "retrying",
        "escalated",
        "failed",
    }

    hass.states.async_set("light.kitchen", "off")

    async def _turn_on(call: ServiceCall) -> None:
        hass.states.async_set(
            "light.kitchen", "on", {"brightness": call.data.get("brightness")}
        )

    hass.services.async_register("light", "turn_on", _turn_on)
    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 200},
        target={"entity_id": "light.kitchen"},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "ok"
    assert state.attributes["entity_id"] == "light.kitchen"
    assert state.attributes["expected_state"] == "on"
    assert isinstance(state.attributes["response_duration"], float)
    assert state.attributes["response_duration"] >= 0
