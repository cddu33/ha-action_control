"""Vacuums end to end: start/pause/stop/return_to_base/clean_spot now have a
modeled expected state (previously a silent no-op -- any rule on `vacuum`
could never detect a real failure).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform

from custom_components.action_control.const import DATA_ENGINE, DOMAIN
from custom_components.action_control.models import Rule, RuleStatus
from tests.conftest import make_entry


class SimVacuum(StateVacuumEntity):
    _attr_should_poll = False
    _attr_supported_features = (
        VacuumEntityFeature.START
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.CLEAN_SPOT
    )

    def __init__(self, name: str, *, obeys: bool = True) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_activity = VacuumActivity.DOCKED
        self.obeys = obeys

    async def async_start(self) -> None:
        if self.obeys:
            self._attr_activity = VacuumActivity.CLEANING
            self.async_write_ha_state()

    async def async_pause(self) -> None:
        if self.obeys:
            self._attr_activity = VacuumActivity.PAUSED
            self.async_write_ha_state()

    async def async_stop(self, **kwargs: Any) -> None:
        if self.obeys:
            self._attr_activity = VacuumActivity.IDLE
            self.async_write_ha_state()

    async def async_return_to_base(self, **kwargs: Any) -> None:
        if self.obeys:
            self._attr_activity = VacuumActivity.RETURNING
            self.async_write_ha_state()

    async def async_clean_spot(self, **kwargs: Any) -> None:
        if self.obeys:
            self._attr_activity = VacuumActivity.CLEANING
            self.async_write_ha_state()


def _vacuum_rule(**overrides: Any) -> Rule:
    fields: dict[str, Any] = dict(
        name="Vacuum watchdog",
        domains=["vacuum"],
        services=["start", "pause", "stop", "return_to_base", "clean_spot"],
        attributes_to_check=[],
        tolerances={},
        retries=1,
        retry_delay=0.02,
        check_delay=0.02,
    )
    fields.update(overrides)
    return Rule(**fields)


async def _setup(hass: HomeAssistant, vacuum: SimVacuum, rule: Rule):
    setup_test_component_platform(hass, "vacuum", [vacuum])
    assert await async_setup_component(hass, "vacuum", {"vacuum": {"platform": "test"}})
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


async def _command(hass: HomeAssistant, entity_id: str, service: str) -> None:
    await hass.services.async_call("vacuum", service, {"entity_id": entity_id})
    await hass.async_block_till_done()


async def test_an_obedient_vacuum_is_never_falsely_reported(hass):
    vacuum = SimVacuum("roomba")
    rule = _vacuum_rule()
    engine, notifications = await _setup(hass, vacuum, rule)

    for service in ("start", "pause", "stop", "return_to_base", "clean_spot"):
        engine.rule_status.clear()
        await _command(hass, vacuum.entity_id, service)
        assert engine.rule_status[rule.rule_id].status is RuleStatus.OK, service

    assert notifications == []


async def test_a_vacuum_that_ignores_start_is_reported(hass):
    vacuum = SimVacuum("stuck", obeys=False)
    rule = _vacuum_rule()
    engine, notifications = await _setup(hass, vacuum, rule)

    await _command(hass, vacuum.entity_id, "start")

    assert engine.rule_status[rule.rule_id].status is RuleStatus.FAILED
    assert len(notifications) == 1
