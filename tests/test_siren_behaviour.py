"""Sirens end to end: a `duration` makes a siren auto-revert to off on its
own, exactly like a light's `flash` -- nothing to verify, and nothing to
replay.
"""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform

from custom_components.action_control.const import DATA_ENGINE, DOMAIN
from custom_components.action_control.models import Rule, RuleStatus
from tests.conftest import make_entry


class SimSiren(SirenEntity):
    _attr_should_poll = False
    _attr_supported_features = (
        SirenEntityFeature.TURN_ON
        | SirenEntityFeature.TURN_OFF
        | SirenEntityFeature.DURATION
        | SirenEntityFeature.TONES
        | SirenEntityFeature.VOLUME_SET
    )
    _attr_available_tones = ["alarm", "chime"]

    def __init__(self, name: str) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_is_on = False
        self.received: list[tuple[str, dict[str, Any]]] = []
        self._revert_handle: asyncio.TimerHandle | None = None

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.received.append(("on", kwargs))
        self._attr_is_on = True
        self.async_write_ha_state()
        duration = kwargs.get("duration")
        if duration:
            self._revert_handle = self.hass.loop.call_later(duration, self._auto_off)

    def _auto_off(self) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()

    def simulate_auto_revert_now(self) -> None:
        """Fire the auto-revert immediately instead of waiting out `duration`."""
        if self._revert_handle is not None:
            self._revert_handle.cancel()
            self._revert_handle = None
        self._auto_off()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.received.append(("off", kwargs))
        self._attr_is_on = False
        self.async_write_ha_state()


def _siren_rule(**overrides: Any) -> Rule:
    fields: dict[str, Any] = dict(
        name="Sirens",
        domains=["siren"],
        services=["turn_on", "turn_off", "toggle"],
        attributes_to_check=[],
        tolerances={},
        retries=1,
        retry_delay=0.05,
        check_delay=0.05,
    )
    fields.update(overrides)
    return Rule(**fields)


async def _setup(hass: HomeAssistant, siren: SimSiren, rule: Rule):
    setup_test_component_platform(hass, "siren", [siren])
    assert await async_setup_component(hass, "siren", {"siren": {"platform": "test"}})
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
    await hass.services.async_call("siren", service, {"entity_id": entity_id, **data})
    await hass.async_block_till_done()


async def test_a_plain_turn_on_still_verifies_normally(hass):
    siren = SimSiren("alarm")
    rule = _siren_rule()
    engine, notifications = await _setup(hass, siren, rule)

    await _command(hass, siren.entity_id, "turn_on", {"tone": "alarm"})

    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []


async def test_turn_on_with_duration_is_never_reported_even_after_auto_revert(hass):
    siren = SimSiren("alarm")
    rule = _siren_rule()
    engine, notifications = await _setup(hass, siren, rule)

    await _command(hass, siren.entity_id, "turn_on", {"duration": 1})
    siren.simulate_auto_revert_now()  # the hardware reverting on its own
    await hass.async_block_till_done()

    assert hass.states.get(siren.entity_id).state == "off"
    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []
    assert len(siren.received) == 1  # nothing replayed


async def test_a_plain_toggle_still_verifies_normally(hass):
    # siren.toggle's schema carries no extra data (not even duration): this
    # locks in that the dispatch refactor didn't change its behavior.
    siren = SimSiren("alarm")
    rule = _siren_rule()
    engine, notifications = await _setup(hass, siren, rule)

    await _command(hass, siren.entity_id, "toggle", {})

    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []


async def test_turn_off_is_unaffected_by_the_duration_exclusion(hass):
    siren = SimSiren("alarm")
    rule = _siren_rule()
    engine, notifications = await _setup(hass, siren, rule)

    await _command(hass, siren.entity_id, "turn_on", {})
    await _command(hass, siren.entity_id, "turn_off", {})

    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []
