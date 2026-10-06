"""Media players end to end: turn_on/turn_off/media_play/media_pause/
media_stop now have a modeled expected state, deliberately permissive
(`turn_on` accepts `on`, `idle`, `playing`, `paused` or `buffering`) so a
player that skips straight from "turning on" to "playing" is never a false
failure.
"""
from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform

from custom_components.action_control.const import DATA_ENGINE, DOMAIN
from custom_components.action_control.models import Rule, RuleStatus
from tests.conftest import make_entry


class SimMediaPlayer(MediaPlayerEntity):
    _attr_should_poll = False
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
    )

    def __init__(self, name: str, *, on_state: str = "playing", obeys: bool = True) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_state = "off"
        self.on_state = on_state
        self.obeys = obeys

    async def async_turn_on(self) -> None:
        if self.obeys:
            self._attr_state = self.on_state
            self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        if self.obeys:
            self._attr_state = "off"
            self.async_write_ha_state()

    async def async_media_play(self) -> None:
        if self.obeys:
            self._attr_state = "playing"
            self.async_write_ha_state()

    async def async_media_pause(self) -> None:
        if self.obeys:
            self._attr_state = "paused"
            self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        if self.obeys:
            self._attr_state = "idle"
            self.async_write_ha_state()


def _player_rule(**overrides: Any) -> Rule:
    fields: dict[str, Any] = dict(
        name="Media player watchdog",
        domains=["media_player"],
        services=["turn_on", "turn_off", "media_play", "media_pause", "media_stop"],
        attributes_to_check=[],
        tolerances={},
        retries=1,
        retry_delay=0.02,
        check_delay=0.02,
    )
    fields.update(overrides)
    return Rule(**fields)


async def _setup(hass: HomeAssistant, player: SimMediaPlayer, rule: Rule):
    setup_test_component_platform(hass, "media_player", [player])
    assert await async_setup_component(
        hass, "media_player", {"media_player": {"platform": "test"}}
    )
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
    await hass.services.async_call("media_player", service, {"entity_id": entity_id})
    await hass.async_block_till_done()


@pytest.mark.parametrize("on_state", ["on", "idle", "playing", "paused", "buffering"])
async def test_turn_on_is_never_falsely_reported_whatever_state_it_lands_on(hass, on_state):
    """A player going straight from "turning on" to idle/playing/paused/
    buffering must not be a false failure -- only a strict "on" and "off"
    were ever asked for."""
    player = SimMediaPlayer("player", on_state=on_state)
    rule = _player_rule()
    engine, notifications = await _setup(hass, player, rule)

    await _command(hass, player.entity_id, "turn_on")

    assert engine.rule_status[rule.rule_id].status is RuleStatus.OK
    assert notifications == []


async def test_an_obedient_player_is_never_falsely_reported(hass):
    player = SimMediaPlayer("player")
    rule = _player_rule()
    engine, notifications = await _setup(hass, player, rule)

    for service in ("turn_on", "media_play", "media_pause", "media_stop", "turn_off"):
        engine.rule_status.clear()
        await _command(hass, player.entity_id, service)
        assert engine.rule_status[rule.rule_id].status is RuleStatus.OK, service

    assert notifications == []


async def test_a_player_that_ignores_turn_on_is_reported(hass):
    player = SimMediaPlayer("stuck", obeys=False)
    rule = _player_rule()
    engine, notifications = await _setup(hass, player, rule)

    await _command(hass, player.entity_id, "turn_on")

    assert engine.rule_status[rule.rule_id].status is RuleStatus.FAILED
    assert len(notifications) == 1
