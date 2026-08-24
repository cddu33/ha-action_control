"""Integration-style tests: call_service event -> verify/retry/notify flow."""
from __future__ import annotations

from homeassistant.core import ServiceCall

from custom_components.action_control.const import DATA_ENGINE, DOMAIN


async def _setup(hass, mock_config_entry):
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][mock_config_entry.entry_id][DATA_ENGINE]


async def test_failed_command_retries_then_notifies(hass, mock_config_entry):
    await _setup(hass, mock_config_entry)

    hass.states.async_set("light.kitchen", "off")
    calls: list[ServiceCall] = []
    notifications: list[dict] = []

    async def _turn_on(call: ServiceCall) -> None:
        # Never actually applies the command: the state stays "off".
        calls.append(call)

    hass.services.async_register("light", "turn_on", _turn_on)
    hass.services.async_register("light", "turn_off", lambda call: None)
    hass.services.async_register("light", "toggle", lambda call: None)

    async def _notify(call: ServiceCall) -> None:
        notifications.append(dict(call.data))

    hass.services.async_register("persistent_notification", "create", _notify)

    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 200},
        target={"entity_id": "light.kitchen"},
        blocking=True,
    )
    await hass.async_block_till_done()

    # initial call + 2 retries (mock_config_entry's rule has retries=2)
    assert len(calls) == 3
    assert len(notifications) == 1
    assert "light.kitchen" in notifications[0]["message"]


async def test_successful_command_does_not_notify(hass, mock_config_entry):
    await _setup(hass, mock_config_entry)

    hass.states.async_set("light.kitchen", "off")
    notifications: list[dict] = []

    async def _turn_on(call: ServiceCall) -> None:
        hass.states.async_set(
            "light.kitchen",
            "on",
            {"brightness": call.data.get("brightness")},
        )

    hass.services.async_register("light", "turn_on", _turn_on)
    hass.services.async_register("light", "turn_off", lambda call: None)
    hass.services.async_register("light", "toggle", lambda call: None)
    hass.services.async_register(
        "persistent_notification",
        "create",
        lambda call: notifications.append(dict(call.data)),
    )

    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 200},
        target={"entity_id": "light.kitchen"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert notifications == []
    state = hass.states.get("light.kitchen")
    assert state.state == "on"
    assert state.attributes.get("brightness") == 200


async def test_already_satisfied_command_exits_immediately_without_retry(
    hass, mock_config_entry
):
    await _setup(hass, mock_config_entry)

    # The target already reflects the requested state/attributes the moment
    # the event fires -- this must short-circuit without any retry or call.
    hass.states.async_set("light.kitchen", "on", {"brightness": 200, "rgb_color": [1, 2, 3]})
    calls: list[ServiceCall] = []

    async def _turn_on(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("light", "turn_on", _turn_on)

    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 200},
        target={"entity_id": "light.kitchen"},
        blocking=True,
    )
    await hass.async_block_till_done()

    # Only the user's own original call went through -- no watchdog retry.
    assert len(calls) == 1
