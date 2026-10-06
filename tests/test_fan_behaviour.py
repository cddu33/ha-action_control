"""Fans end to end: set_percentage/oscillate/set_preset_mode already derive
their expected value from the generic same-name fallback (no
SERVICE_DATA_ATTRIBUTE_SOURCES entry was needed -- the service key already
matches the attribute name); increase_speed/decrease_speed are relative and
carry no absolute percentage, so they already resolve to no expected
state/attributes and must never produce a false failure.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform

from custom_components.action_control.const import DATA_ENGINE, DOMAIN
from custom_components.action_control.domain_defaults import DOMAIN_PRESETS
from custom_components.action_control.models import Rule, RuleStatus
from tests.conftest import make_entry


class SimFan(FanEntity):
    _attr_should_poll = False
    _attr_supported_features = (
        FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.SET_SPEED
        | FanEntityFeature.OSCILLATE
        | FanEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = ["eco", "turbo"]

    def __init__(self, name: str, *, obeys: bool = True) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_is_on = False
        self._attr_percentage = 0
        self._attr_oscillating = False
        self._attr_preset_mode = None
        self.obeys = obeys
        self.received: list[tuple[str, dict[str, Any]]] = []

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.received.append(("on", kwargs))
        self._attr_is_on = True
        if "percentage" in kwargs:
            self._attr_percentage = kwargs["percentage"]
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        if self.obeys:
            self._attr_is_on = percentage > 0
            self._attr_percentage = percentage
            self.async_write_ha_state()

    async def async_oscillate(self, oscillating: bool) -> None:
        self._attr_oscillating = oscillating
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()


def _fan_rule(**overrides: Any) -> Rule:
    preset = DOMAIN_PRESETS["fan"]
    fields: dict[str, Any] = dict(
        name="Fan watchdog",
        domains=["fan"],
        services=[
            "turn_on",
            "turn_off",
            "set_percentage",
            "oscillate",
            "set_preset_mode",
            "increase_speed",
            "decrease_speed",
        ],
        attributes_to_check=list(preset["attributes_to_check"]),
        tolerances=dict(preset["tolerances"]),
        retries=1,
        retry_delay=0.02,
        check_delay=0.02,
    )
    fields.update(overrides)
    return Rule(**fields)


async def _setup(hass: HomeAssistant, fan: SimFan, rule: Rule):
    setup_test_component_platform(hass, "fan", [fan])
    assert await async_setup_component(hass, "fan", {"fan": {"platform": "test"}})
    entry = make_entry(rule)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    notifications: list[str] = []
    hass.services.async_register(
        "persistent_notification",
        "create",
        lambda call: notifications.append(call.data["message"]),
    )
    return hass.data[DOMAIN][entry.entry_id][DATA_ENGINE], notifications


async def _command(hass: HomeAssistant, entity_id: str, service: str, data: dict) -> None:
    await hass.services.async_call("fan", service, {"entity_id": entity_id, **data})
    await hass.async_block_till_done()


async def test_set_percentage_verifies_against_the_percentage_attribute(hass):
    fan = SimFan("ceiling")
    rule = _fan_rule()
    engine, notifications = await _setup(hass, fan, rule)

    await _command(hass, fan.entity_id, "set_percentage", {"percentage": 60})

    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []


async def test_a_fan_that_ignores_set_percentage_is_reported(hass):
    fan = SimFan("stuck", obeys=False)
    rule = _fan_rule()
    engine, notifications = await _setup(hass, fan, rule)

    await _command(hass, fan.entity_id, "set_percentage", {"percentage": 60})

    assert engine.rule_status[rule.rule_id].status is RuleStatus.FAILED
    assert len(notifications) == 1


async def test_oscillate_and_set_preset_mode_are_never_falsely_reported(hass):
    fan = SimFan("ceiling")
    rule = _fan_rule(attributes_to_check=["oscillating", "preset_mode"], tolerances={})
    engine, notifications = await _setup(hass, fan, rule)

    await _command(hass, fan.entity_id, "oscillate", {"oscillating": True})
    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK

    engine.rule_status.clear()
    await _command(hass, fan.entity_id, "set_preset_mode", {"preset_mode": "eco"})
    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []


async def test_increase_and_decrease_speed_are_never_falsely_reported(hass):
    """Relative, with no absolute percentage in their data: nothing to
    verify, and nothing the entity could ever be "wrong" about."""
    fan = SimFan("ceiling")
    rule = _fan_rule()
    engine, notifications = await _setup(hass, fan, rule)

    await _command(hass, fan.entity_id, "increase_speed", {"percentage_step": 10})
    await _command(hass, fan.entity_id, "decrease_speed", {"percentage_step": 10})

    assert notifications == []
    if rule.rule_id in engine.rule_status:
        assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
