"""Tests for the run_rule and reset_escalation_cooldown services."""
from __future__ import annotations

import pytest
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.action_control.const import DATA_ENGINE, DOMAIN
from tests.conftest import make_cover_rule, make_entry, make_light_rule


async def _setup(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id][DATA_ENGINE]


def _rule_sensor_entity_id(hass, entry, rule_id: str) -> str:
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{rule_id}"
    )


async def test_run_rule_uses_the_rule_s_default_service(hass):
    rule = make_light_rule()
    entry = make_entry(rule)
    engine = await _setup(hass, entry)
    rule_sensor = _rule_sensor_entity_id(hass, entry, rule.rule_id)

    hass.states.async_set("light.kitchen", "off")
    calls: list[ServiceCall] = []

    async def _turn_on(call: ServiceCall) -> None:
        calls.append(call)
        hass.states.async_set("light.kitchen", "on", {"brightness": call.data.get("brightness")})

    hass.services.async_register("light", "turn_on", _turn_on)

    await hass.services.async_call(
        DOMAIN,
        "run_rule",
        {
            "rule_sensor": rule_sensor,
            "entity_id": "light.kitchen",
            "service_data": {"brightness": 150},
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data.get("brightness") == 150
    assert engine.rule_status[rule.rule_id].status.value == "ok"


async def test_run_rule_can_override_the_service(hass):
    rule = make_light_rule()
    entry = make_entry(rule)
    await _setup(hass, entry)
    rule_sensor = _rule_sensor_entity_id(hass, entry, rule.rule_id)

    hass.states.async_set("light.kitchen", "on")
    toggled: list[ServiceCall] = []

    async def _toggle(call: ServiceCall) -> None:
        toggled.append(call)
        current = hass.states.get("light.kitchen")
        hass.states.async_set("light.kitchen", "off" if current.state == "on" else "on")

    hass.services.async_register("light", "turn_on", lambda call: None)
    hass.services.async_register("light", "toggle", _toggle)

    await hass.services.async_call(
        DOMAIN,
        "run_rule",
        {
            "rule_sensor": rule_sensor,
            "entity_id": "light.kitchen",
            "service_data": {"service": "toggle"},
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(toggled) == 1


async def test_run_rule_rejects_entity_outside_rule_domains(hass):
    rule = make_light_rule()
    entry = make_entry(rule)
    await _setup(hass, entry)
    rule_sensor = _rule_sensor_entity_id(hass, entry, rule.rule_id)

    hass.states.async_set("switch.garage", "off")

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "run_rule",
            {"rule_sensor": rule_sensor, "entity_id": "switch.garage"},
            blocking=True,
        )


async def test_run_rule_rejects_a_sensor_that_is_not_a_rule(hass):
    rule = make_light_rule()
    entry = make_entry(rule)
    await _setup(hass, entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "run_rule",
            {"rule_sensor": "sensor.not_a_rule", "entity_id": "light.kitchen"},
            blocking=True,
        )


async def test_reset_escalation_cooldown(hass):
    rule = make_cover_rule()
    entry = make_entry(rule)
    engine = await _setup(hass, entry)
    rule_sensor = _rule_sensor_entity_id(hass, entry, rule.rule_id)

    engine.arm_escalation_cooldown(rule.rule_id, 300)
    assert not engine.escalation_ready(rule.rule_id)

    await hass.services.async_call(
        DOMAIN,
        "reset_escalation_cooldown",
        {"rule_sensor": rule_sensor},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert engine.escalation_ready(rule.rule_id)
