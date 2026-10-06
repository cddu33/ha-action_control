"""Climate/humidifier end to end: Home Assistant rejects an out-of-range
set_temperature/set_humidity before the entity ever sees it (no clamp, no
state change) -- a rule that expects the raw requested value would fail
every single time. These tests drive the real climate/humidifier
components to confirm the exclusion added in comparator.py matches.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.humidifier import HumidifierEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform

from custom_components.action_control.const import DATA_ENGINE, DOMAIN
from custom_components.action_control.models import Rule, RuleStatus
from tests.conftest import make_entry


class SimClimate(ClimateEntity):
    _attr_should_poll = False
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]

    def __init__(self, name: str, *, min_temp: float = 7, max_temp: float = 35) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_min_temp = min_temp
        self._attr_max_temp = max_temp
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_target_temperature = 20.0

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if ATTR_TEMPERATURE in kwargs:
            self._attr_target_temperature = kwargs[ATTR_TEMPERATURE]
            self.async_write_ha_state()


class SimHumidifier(HumidifierEntity):
    _attr_should_poll = False

    def __init__(self, name: str, *, min_humidity: float = 30, max_humidity: float = 80) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_min_humidity = min_humidity
        self._attr_max_humidity = max_humidity
        self._attr_is_on = True
        self._attr_target_humidity = 50

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_set_humidity(self, humidity: int) -> None:
        self._attr_target_humidity = humidity
        self.async_write_ha_state()


def _rule(domain: str, service: str, attribute: str, **overrides: Any) -> Rule:
    fields: dict[str, Any] = dict(
        name=f"{domain} watchdog",
        domains=[domain],
        services=[service],
        attributes_to_check=[attribute],
        tolerances={attribute: 0},
        retries=1,
        retry_delay=0,
        check_delay=0.01,
    )
    fields.update(overrides)
    return Rule(**fields)


async def _setup_climate(hass: HomeAssistant, climate: SimClimate, rule: Rule):
    setup_test_component_platform(hass, "climate", [climate])
    assert await async_setup_component(hass, "climate", {"climate": {"platform": "test"}})
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


async def _setup_humidifier(hass: HomeAssistant, humidifier: SimHumidifier, rule: Rule):
    setup_test_component_platform(hass, "humidifier", [humidifier])
    assert await async_setup_component(hass, "humidifier", {"humidifier": {"platform": "test"}})
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


async def test_in_range_set_temperature_still_verifies_normally(hass):
    climate = SimClimate("thermostat")
    rule = _rule("climate", "set_temperature", "temperature")
    engine, notifications = await _setup_climate(hass, climate, rule)

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate.entity_id, "temperature": 21},
    )
    await hass.async_block_till_done()

    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []


async def test_out_of_range_set_temperature_is_never_reported(hass):
    """Home Assistant rejects it outright: nothing to verify, no retry."""
    climate = SimClimate("thermostat")
    rule = _rule("climate", "set_temperature", "temperature")
    engine, notifications = await _setup_climate(hass, climate, rule)

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate.entity_id, "temperature": 40},
    )
    await hass.async_block_till_done()

    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []
    # Home Assistant's own validation rejected the call before the entity
    # ever saw it -- the target temperature never moved from its default.
    assert climate.target_temperature == 20.0


async def test_below_range_set_temperature_is_never_reported(hass):
    climate = SimClimate("thermostat")
    rule = _rule("climate", "set_temperature", "temperature")
    engine, notifications = await _setup_climate(hass, climate, rule)

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate.entity_id, "temperature": 2},
    )
    await hass.async_block_till_done()

    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []


async def test_in_range_set_humidity_still_verifies_normally(hass):
    humidifier = SimHumidifier("humidifier")
    rule = _rule("humidifier", "set_humidity", "humidity")
    engine, notifications = await _setup_humidifier(hass, humidifier, rule)

    await hass.services.async_call(
        "humidifier",
        "set_humidity",
        {"entity_id": humidifier.entity_id, "humidity": 55},
    )
    await hass.async_block_till_done()

    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []


async def test_out_of_range_set_humidity_is_never_reported(hass):
    humidifier = SimHumidifier("humidifier")
    rule = _rule("humidifier", "set_humidity", "humidity")
    engine, notifications = await _setup_humidifier(hass, humidifier, rule)

    await hass.services.async_call(
        "humidifier",
        "set_humidity",
        {"entity_id": humidifier.entity_id, "humidity": 95},
    )
    await hass.async_block_till_done()

    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []
    assert humidifier.target_humidity == 50
