"""Integration-style tests: call_service event -> verify/retry/notify flow."""
from __future__ import annotations

import asyncio
import logging

import pytest
from homeassistant.core import ServiceCall
from homeassistant.setup import async_setup_component

from custom_components.action_control import watchdog
from custom_components.action_control.const import (
    DATA_ENGINE,
    DOMAIN,
    MAX_RETRY_DELAY,
    RETRY_BACKOFF_CONSTANT,
    RETRY_BACKOFF_EXPONENTIAL,
    RETRY_BACKOFF_LINEAR,
)
from custom_components.action_control.models import Rule, RuleStatus
from tests.conftest import make_cover_rule, make_entry, make_light_rule


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


async def test_cover_without_position_feedback_falls_back_to_state_check(hass):
    """A plain open/close cover that never reports current_position must
    not time out waiting for an attribute that never changes -- it falls
    back to the normal open/opening state check instead."""
    rule = make_cover_rule(retries=0, check_delay=0.01)
    entry = make_entry(rule)
    await _setup(hass, entry)

    hass.states.async_set("cover.volet_salon", "closed")  # no current_position at all

    async def _open(call: ServiceCall) -> None:
        hass.states.async_set("cover.volet_salon", "open")

    hass.services.async_register("cover", "open_cover", _open)
    notifications: list[dict] = []
    hass.services.async_register(
        "persistent_notification", "create", lambda call: notifications.append(dict(call.data))
    )

    await hass.services.async_call(
        "cover", "open_cover", target={"entity_id": "cover.volet_salon"}, blocking=True
    )
    await hass.async_block_till_done()

    assert notifications == []


async def test_cover_without_position_feedback_still_fails_if_it_never_opens(hass):
    rule = make_cover_rule(retries=0, check_delay=0.01)
    entry = make_entry(rule)
    await _setup(hass, entry)

    hass.states.async_set("cover.volet_salon", "closed")  # no current_position at all

    hass.services.async_register("cover", "open_cover", lambda call: None)
    notifications: list[dict] = []
    hass.services.async_register(
        "persistent_notification", "create", lambda call: notifications.append(dict(call.data))
    )

    await hass.services.async_call(
        "cover", "open_cover", target={"entity_id": "cover.volet_salon"}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(notifications) == 1


async def test_cover_with_position_feedback_still_uses_movement_mode(hass):
    """Regression: a cover that does report current_position must keep
    waiting for it to move rather than falling back to a state check."""
    rule = make_cover_rule(retries=0, change_timeout=0.05)
    entry = make_entry(rule)
    await _setup(hass, entry)

    # Reports "open" immediately but current_position never actually moves:
    # Movement mode must still fail this, proving the fallback didn't kick in.
    hass.states.async_set("cover.volet_salon", "closed", {"current_position": 0})

    async def _open(call: ServiceCall) -> None:
        hass.states.async_set("cover.volet_salon", "open", {"current_position": 0})

    hass.services.async_register("cover", "open_cover", _open)
    notifications: list[dict] = []
    hass.services.async_register(
        "persistent_notification", "create", lambda call: notifications.append(dict(call.data))
    )

    await hass.services.async_call(
        "cover", "open_cover", target={"entity_id": "cover.volet_salon"}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(notifications) == 1


async def test_escalation_runs_once_for_several_failing_entities(
    hass, mock_cover_config_entry
):
    """The cooldown must be armed before the action runs, not after it."""
    await _setup(hass, mock_cover_config_entry)

    for entity_id in ("cover.volet_salon", "cover.volet_cuisine"):
        hass.states.async_set(entity_id, "closed", {"current_position": 0})

    restarts: list[ServiceCall] = []
    hass.services.async_register("cover", "open_cover", lambda call: None)
    hass.services.async_register("script", "restart_gateway", lambda call: restarts.append(call))
    hass.services.async_register("persistent_notification", "create", lambda call: None)

    await hass.services.async_call(
        "cover",
        "open_cover",
        target={"entity_id": ["cover.volet_salon", "cover.volet_cuisine"]},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(restarts) == 1


async def test_escalation_check_passes_without_retrying_the_action(hass):
    """The escalation action reaching the expected state on the first try
    must not trigger any extra retry of that action."""
    rule = make_cover_rule(
        escalation_check_entity_id="switch.gateway_restart",
        escalation_check_state="on",
        escalation_check_delay=0,
    )
    entry = make_entry(rule)
    await _setup(hass, entry)

    hass.states.async_set("cover.volet_salon", "closed", {"current_position": 0})
    restarts: list[ServiceCall] = []

    async def _restart(call: ServiceCall) -> None:
        restarts.append(call)
        hass.states.async_set("switch.gateway_restart", "on")

    hass.services.async_register("cover", "open_cover", lambda call: None)
    hass.services.async_register("script", "restart_gateway", _restart)
    hass.services.async_register("persistent_notification", "create", lambda call: None)

    await hass.services.async_call(
        "cover", "open_cover", target={"entity_id": "cover.volet_salon"}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(restarts) == 1


async def test_escalation_check_retries_the_action_until_confirmed(hass):
    rule = make_cover_rule(
        escalation_check_entity_id="switch.gateway_restart",
        escalation_check_state="on",
        escalation_check_delay=0,
        retries=2,
        retry_delay=0,
    )
    entry = make_entry(rule)
    await _setup(hass, entry)

    hass.states.async_set("cover.volet_salon", "closed", {"current_position": 0})
    hass.states.async_set("switch.gateway_restart", "off")
    restarts: list[ServiceCall] = []

    async def _restart(call: ServiceCall) -> None:
        # Only takes effect on the second run.
        if len(restarts) == 1:
            hass.states.async_set("switch.gateway_restart", "on")
        restarts.append(call)

    hass.services.async_register("cover", "open_cover", lambda call: None)
    hass.services.async_register("script", "restart_gateway", _restart)
    hass.services.async_register("persistent_notification", "create", lambda call: None)

    await hass.services.async_call(
        "cover", "open_cover", target={"entity_id": "cover.volet_salon"}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(restarts) == 2
    assert hass.states.get("switch.gateway_restart").state == "on"


async def test_escalation_check_still_replays_command_after_exhausting_retries(hass):
    """Even if the escalation target never confirms, the original command
    must still be replayed as a last resort."""
    rule = make_cover_rule(
        escalation_check_entity_id="switch.gateway_restart",
        escalation_check_state="on",
        escalation_check_delay=0,
        retries=1,
        retry_delay=0,
    )
    entry = make_entry(rule)
    await _setup(hass, entry)

    hass.states.async_set("cover.volet_salon", "closed", {"current_position": 0})
    hass.states.async_set("switch.gateway_restart", "off")
    reopen_calls: list[ServiceCall] = []

    hass.services.async_register(
        "cover", "open_cover", lambda call: reopen_calls.append(call)
    )
    hass.services.async_register("script", "restart_gateway", lambda call: None)
    hass.services.async_register("persistent_notification", "create", lambda call: None)

    await hass.services.async_call(
        "cover", "open_cover", target={"entity_id": "cover.volet_salon"}, blocking=True
    )
    await hass.async_block_till_done()

    # initial call + 1 move-detection retry (retries=1) + the replay after
    # the (unconfirmed) escalation.
    assert len(reopen_calls) == 3
    assert hass.states.get("switch.gateway_restart").state == "off"


async def test_escalation_check_without_a_state_is_not_attempted(hass):
    """A rule saved before the state became mandatory has the entity set but
    no state to compare against. That check can never pass, so it must not
    re-run the recovery action at all."""
    rule = make_cover_rule(
        escalation_check_entity_id="switch.gateway_restart",
        escalation_check_state=None,
        escalation_check_delay=0,
        retries=2,
        retry_delay=0,
    )
    entry = make_entry(rule)
    await _setup(hass, entry)

    hass.states.async_set("cover.volet_salon", "closed", {"current_position": 0})
    hass.states.async_set("switch.gateway_restart", "off")
    restarts: list[ServiceCall] = []

    hass.services.async_register("cover", "open_cover", lambda call: None)
    hass.services.async_register(
        "script", "restart_gateway", lambda call: restarts.append(call)
    )
    hass.services.async_register("persistent_notification", "create", lambda call: None)

    await hass.services.async_call(
        "cover", "open_cover", target={"entity_id": "cover.volet_salon"}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(restarts) == 1


async def test_a_failing_command_still_reports(hass, mock_config_entry):
    """A service call that raises must not kill the run silently."""
    await _setup(hass, mock_config_entry)

    hass.states.async_set("light.kitchen", "off")
    notifications: list[dict] = []

    async def _turn_on(call: ServiceCall) -> None:
        raise RuntimeError("device offline")

    hass.services.async_register("light", "turn_on", _turn_on)
    hass.services.async_register(
        "persistent_notification",
        "create",
        lambda call: notifications.append(dict(call.data)),
    )

    with pytest.raises(RuntimeError):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"brightness": 200},
            target={"entity_id": "light.kitchen"},
            blocking=True,
        )
    await hass.async_block_till_done()

    assert len(notifications) == 1


async def test_a_failing_notification_does_not_skip_the_notify_service(hass):
    entry = make_entry(make_light_rule(notify_service="mobile"))
    engine = await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "off")
    notified: list[ServiceCall] = []

    async def _boom(call: ServiceCall) -> None:
        raise RuntimeError("notifications are down")

    hass.services.async_register("light", "turn_on", lambda call: None)
    hass.services.async_register("persistent_notification", "create", _boom)
    hass.services.async_register("notify", "mobile", lambda call: notified.append(call))

    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 200},
        target={"entity_id": "light.kitchen"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(notified) == 1
    rule_id = next(iter(engine.rules))
    assert engine.rule_status[rule_id].status is RuleStatus.FAILED


async def test_global_switch_off_watches_nothing(hass):
    entry = make_entry(make_light_rule(), enabled=False)
    await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "off")
    calls: list[ServiceCall] = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 200},
        target={"entity_id": "light.kitchen"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(calls) == 1


async def test_a_superseded_run_is_dropped(hass, mock_config_entry):
    """A newer command for the same entity invalidates a queued check."""
    engine = await _setup(hass, mock_config_entry)

    hass.states.async_set("light.kitchen", "off")
    calls: list[ServiceCall] = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))

    rule = next(iter(engine.rules.values()))
    engine.next_run_token(rule.rule_id, "light.kitchen")

    await watchdog.async_run_watchdog(
        engine,
        rule,
        "light.kitchen",
        "light",
        "turn_on",
        {"brightness": 200},
        frozenset({"on"}),
        {"brightness": 200},
        run_token=0,
    )

    assert calls == []


async def test_a_superseded_run_does_not_wait_for_the_lock(hass, mock_config_entry):
    """The lock is held for a whole run, sleeps included. An already-obsolete
    check must bail out instead of queueing behind it."""
    engine = await _setup(hass, mock_config_entry)

    hass.states.async_set("light.kitchen", "off")
    hass.services.async_register("light", "turn_on", lambda call: None)

    rule = next(iter(engine.rules.values()))
    engine.next_run_token(rule.rule_id, "light.kitchen")

    # Stand in for a run still in flight and holding the lock.
    lock = engine.lock_for(rule.rule_id, "light.kitchen")
    await lock.acquire()
    try:
        await asyncio.wait_for(
            watchdog.async_run_watchdog(
                engine,
                rule,
                "light.kitchen",
                "light",
                "turn_on",
                {"brightness": 200},
                frozenset({"on"}),
                {"brightness": 200},
                run_token=0,
            ),
            timeout=1,
        )
    finally:
        lock.release()


async def test_a_run_superseded_during_the_check_reports_nothing(hass):
    """An opposite command landing mid-check must not be reported as a
    failure: the entity is where the newer command asked it to be."""
    # retries=0, so the retry loop body never runs and the check goes straight
    # from the comparison to the failure path.
    entry = make_entry(make_light_rule(retries=0, check_delay=0.05))
    engine = await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "off")
    notifications: list[dict] = []
    hass.services.async_register("light", "turn_on", lambda call: None)
    hass.services.async_register(
        "persistent_notification",
        "create",
        lambda call: notifications.append(dict(call.data)),
    )
    rule = next(iter(engine.rules.values()))

    async def _newer_command() -> None:
        # Well inside the 0.05 s the run spends asleep in check_delay.
        await asyncio.sleep(0.01)
        engine.next_run_token(rule.rule_id, "light.kitchen")

    hass.async_create_task(_newer_command())
    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 200},
        target={"entity_id": "light.kitchen"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert notifications == []
    # A superseded run publishes nothing at all, so there may be no status yet;
    # what matters is that it never reports a failure.
    status = engine.rule_status.get(rule.rule_id)
    assert status is None or status.status is not RuleStatus.FAILED


async def _wait_until(predicate, timeout: float = 1) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout)


async def test_a_newer_command_cancels_the_check_in_progress(hass):
    """Turn on, then off a few seconds later: the "on" check is dropped on
    the spot, and the "off" one is verified without queueing behind it."""
    entry = make_entry(make_light_rule(check_delay=30))
    engine = await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "off")
    turn_on_calls: list[ServiceCall] = []
    # Never applies: left alone, the "on" check would sleep, then retry.
    hass.services.async_register("light", "turn_on", lambda call: turn_on_calls.append(call))
    hass.services.async_register("light", "turn_off", lambda call: None)
    rule = next(iter(engine.rules.values()))

    await hass.services.async_call(
        "light", "turn_on", target={"entity_id": "light.kitchen"}, blocking=True
    )
    await hass.services.async_call(
        "light", "turn_off", target={"entity_id": "light.kitchen"}, blocking=True
    )
    # Would take the full 30 s check_delay if the "on" check still held the lock.
    await asyncio.wait_for(hass.async_block_till_done(), 1)

    assert len(turn_on_calls) == 1  # the user's own call, never retried
    status = engine.rule_status[rule.rule_id]
    assert status.status is RuleStatus.OK
    assert status.expected_state == "off"


async def test_a_newer_command_stops_the_replay_but_not_the_recovery_action(hass):
    """Cancelled mid-escalation, the recovery action still runs to its end --
    stopping it halfway could leave a gateway switched off -- but the old
    command is not replayed after it."""
    rule = make_cover_rule(retries=0, escalation_replay_delay=30)
    await _setup(hass, make_entry(rule))

    hass.states.async_set("cover.volet_salon", "closed", {"current_position": 0})
    open_calls: list[ServiceCall] = []
    restarts: list[str] = []

    async def _restart(call: ServiceCall) -> None:
        restarts.append("started")
        await asyncio.sleep(0.1)
        restarts.append("finished")

    hass.services.async_register("cover", "open_cover", lambda call: open_calls.append(call))
    hass.services.async_register("cover", "close_cover", lambda call: None)
    hass.services.async_register("script", "restart_gateway", _restart)
    hass.services.async_register("persistent_notification", "create", lambda call: None)

    await hass.services.async_call(
        "cover", "open_cover", target={"entity_id": "cover.volet_salon"}, blocking=True
    )
    await _wait_until(lambda: restarts)
    await hass.services.async_call(
        "cover", "close_cover", target={"entity_id": "cover.volet_salon"}, blocking=True
    )
    await asyncio.wait_for(hass.async_block_till_done(), 1)

    assert restarts == ["started", "finished"]
    assert len(open_calls) == 1  # no replay of the cancelled "open"


async def test_a_command_the_rule_does_not_watch_still_cancels_its_check(hass):
    """A rule watching only turn_on must drop its check when the light is
    turned off -- and above all not retry, switching it back on."""
    entry = make_entry(make_light_rule(services=["turn_on"], check_delay=0.1))
    engine = await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "off")
    turn_on_calls: list[ServiceCall] = []
    notifications: list[dict] = []
    hass.services.async_register("light", "turn_on", lambda call: turn_on_calls.append(call))
    hass.services.async_register(
        "light", "turn_off", lambda call: hass.states.async_set("light.kitchen", "off")
    )
    hass.services.async_register(
        "persistent_notification",
        "create",
        lambda call: notifications.append(dict(call.data)),
    )

    await hass.services.async_call(
        "light", "turn_on", target={"entity_id": "light.kitchen"}, blocking=True
    )
    await hass.services.async_call(
        "light", "turn_off", target={"entity_id": "light.kitchen"}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(turn_on_calls) == 1
    assert notifications == []
    assert engine._runs == {}  # noqa: SLF001


async def test_homeassistant_turn_off_cancels_a_light_check(hass):
    """homeassistant.turn_off reaches the light as a light.turn_off call of
    its own, so it overrides a check like any other command."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = make_entry(make_light_rule(services=["turn_on"], check_delay=0.1))
    await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "off")
    notifications: list[dict] = []
    hass.services.async_register("light", "turn_on", lambda call: None)
    hass.services.async_register(
        "light", "turn_off", lambda call: hass.states.async_set("light.kitchen", "off")
    )
    hass.services.async_register(
        "persistent_notification",
        "create",
        lambda call: notifications.append(dict(call.data)),
    )

    await hass.services.async_call(
        "light", "turn_on", target={"entity_id": "light.kitchen"}, blocking=True
    )
    await hass.services.async_call(
        "homeassistant", "turn_off", target={"entity_id": "light.kitchen"}, blocking=True
    )
    await hass.async_block_till_done()

    assert notifications == []


async def test_a_light_transition_is_waited_for_before_checking(hass):
    """Checked mid-fade, a light looks wrong, and the retry restarts the fade."""
    entry = make_entry(make_light_rule(check_delay=0, retries=1))
    await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "off")
    turn_on_calls: list[ServiceCall] = []

    async def _fade_in(call: ServiceCall) -> None:
        turn_on_calls.append(call)
        await asyncio.sleep(0.1)
        hass.states.async_set("light.kitchen", "on", {"brightness": 200})

    hass.services.async_register("light", "turn_on", _fade_in)
    hass.services.async_register("persistent_notification", "create", lambda call: None)

    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 200, "transition": 0.2},
        target={"entity_id": "light.kitchen"},
    )
    await hass.async_block_till_done()

    assert len(turn_on_calls) == 1  # never re-issued


async def test_turning_a_light_on_at_brightness_zero_expects_it_off(hass):
    entry = make_entry(make_light_rule())
    await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "on", {"brightness": 200})
    notifications: list[dict] = []
    hass.services.async_register(
        "light", "turn_on", lambda call: hass.states.async_set("light.kitchen", "off")
    )
    hass.services.async_register(
        "persistent_notification",
        "create",
        lambda call: notifications.append(dict(call.data)),
    )

    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 0},
        target={"entity_id": "light.kitchen"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert notifications == []


# ---- retry backoff ----


def test_a_toggle_is_replayed_as_the_service_it_stood_for():
    replay = watchdog._reissued_call  # noqa: SLF001

    assert replay("light", "toggle", {"brightness": 200}, frozenset({"on"})) == (
        "turn_on",
        {"brightness": 200},
    )
    # turn_off rejects brightness: only a transition is carried over.
    assert replay(
        "light", "toggle", {"brightness": 200, "transition": 2}, frozenset({"off"})
    ) == ("turn_off", {"transition": 2})
    assert replay("cover", "toggle", {}, frozenset({"open", "opening"})) == ("open_cover", {})
    assert replay("valve", "toggle", {}, frozenset({"closed", "closing"})) == (
        "close_valve",
        {},
    )
    # Relative steps and flashes are never replayed; targets are dropped.
    assert replay(
        "light",
        "turn_on",
        {"entity_id": "light.x", "brightness_step_pct": 10, "flash": "short"},
        frozenset({"on"}),
    ) == ("turn_on", {})


def test_compute_retry_delay_constant_ignores_attempt():
    for attempt in (1, 2, 5):
        assert watchdog._compute_retry_delay(5, RETRY_BACKOFF_CONSTANT, attempt) == 5


def test_compute_retry_delay_linear_scales_with_attempt():
    assert watchdog._compute_retry_delay(2, RETRY_BACKOFF_LINEAR, 1) == 2
    assert watchdog._compute_retry_delay(2, RETRY_BACKOFF_LINEAR, 3) == 6


def test_compute_retry_delay_exponential_doubles_each_time():
    assert watchdog._compute_retry_delay(1, RETRY_BACKOFF_EXPONENTIAL, 1) == 1
    assert watchdog._compute_retry_delay(1, RETRY_BACKOFF_EXPONENTIAL, 2) == 2
    assert watchdog._compute_retry_delay(1, RETRY_BACKOFF_EXPONENTIAL, 4) == 8


def test_compute_retry_delay_is_capped():
    delay = watchdog._compute_retry_delay(1000, RETRY_BACKOFF_EXPONENTIAL, 10)
    assert delay == MAX_RETRY_DELAY


async def test_exponential_backoff_still_retries_and_notifies(hass):
    """Backoff mode must not break the existing retry/notify wiring."""
    rule = make_light_rule(
        retry_backoff=RETRY_BACKOFF_EXPONENTIAL, retries=3, retry_delay=0.01
    )
    entry = make_entry(rule)
    await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "off")
    calls: list[ServiceCall] = []
    notifications: list[dict] = []
    hass.services.async_register(
        "light", "turn_on", lambda call: calls.append(call)
    )
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

    # initial call + 3 retries
    assert len(calls) == 4
    assert len(notifications) == 1


# ---- response_duration ----


async def test_response_duration_is_recorded(hass, mock_config_entry):
    engine = await _setup(hass, mock_config_entry)

    hass.states.async_set("light.kitchen", "on", {"brightness": 200, "rgb_color": [1, 2, 3]})
    hass.services.async_register("light", "turn_on", lambda call: None)

    await hass.services.async_call(
        "light",
        "turn_on",
        {"brightness": 200},
        target={"entity_id": "light.kitchen"},
        blocking=True,
    )
    await hass.async_block_till_done()

    rule_id = next(iter(engine.rules))
    duration = engine.rule_status[rule_id].response_duration
    assert isinstance(duration, float)
    assert duration >= 0


# ---- domains with nothing meaningful to compare (e.g. scenes) ----


async def test_scene_activation_resolves_immediately_without_retry(hass):
    rule = Rule(
        name="Scene watchdog",
        domains=["scene"],
        services=["turn_on"],
        retries=2,
        retry_delay=0,
        check_delay=0,
    )
    entry = make_entry(rule)
    engine = await _setup(hass, entry)

    hass.states.async_set("scene.movie_night", "2024-01-01T00:00:00+00:00")
    calls: list[ServiceCall] = []
    hass.services.async_register("scene", "turn_on", lambda call: calls.append(call))

    await hass.services.async_call(
        "scene",
        "turn_on",
        target={"entity_id": "scene.movie_night"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert len(calls) == 1  # only the original call, no retry
    rule_id = next(iter(engine.rules))
    assert engine.rule_status[rule_id].status is RuleStatus.OK


# ---- "verified OK" debug message must not overclaim a retry ----


async def test_settling_during_check_delay_is_not_logged_as_a_retry(hass, caplog):
    """The entity catching up during the plain check_delay wait, with no
    command ever reissued, must not be logged the same way a real retry
    is -- that would make every such settle look like a retry when
    grepping the debug log."""
    rule = make_light_rule(retries=2)
    entry = make_entry(rule)
    await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "off")

    async def _turn_on(call) -> None:
        hass.states.async_set(
            "light.kitchen", "on", {"brightness": call.data.get("brightness")}
        )

    hass.services.async_register("light", "turn_on", _turn_on)

    with caplog.at_level(logging.DEBUG, logger="custom_components.action_control.watchdog"):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"brightness": 200},
            target={"entity_id": "light.kitchen"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert "no retry needed" in caplog.text
    assert "verified OK after retry" not in caplog.text


async def test_a_genuine_retry_is_still_logged_as_a_retry(hass, caplog):
    rule = make_light_rule(retries=2, retry_delay=0)
    entry = make_entry(rule)
    await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "off")
    calls = []

    async def _turn_on(call) -> None:
        calls.append(call)
        if len(calls) >= 2:
            # Only the reissued (second) call actually applies it.
            hass.states.async_set(
                "light.kitchen", "on", {"brightness": call.data.get("brightness")}
            )

    hass.services.async_register("light", "turn_on", _turn_on)

    with caplog.at_level(logging.DEBUG, logger="custom_components.action_control.watchdog"):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"brightness": 200},
            target={"entity_id": "light.kitchen"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert "verified OK after retry" in caplog.text
    assert len(calls) == 2


# ---- per-rule info-level log ----


async def test_log_entity_info_emits_an_info_summary_when_enabled(hass, caplog):
    rule = make_light_rule(log_entity_info=True)
    entry = make_entry(rule)
    await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "on", {"brightness": 200, "rgb_color": [1, 2, 3]})
    hass.services.async_register("light", "turn_on", lambda call: None)

    with caplog.at_level(
        logging.INFO, logger="custom_components.action_control.watchdog"
    ):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"brightness": 200},
            target={"entity_id": "light.kitchen"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert "light.kitchen" in caplog.text
    assert "-> ok" in caplog.text


async def test_log_entity_info_is_silent_by_default(hass, caplog):
    entry = make_entry(make_light_rule())  # log_entity_info defaults to False
    await _setup(hass, entry)

    hass.states.async_set("light.kitchen", "on", {"brightness": 200, "rgb_color": [1, 2, 3]})
    hass.services.async_register("light", "turn_on", lambda call: None)

    with caplog.at_level(
        logging.INFO, logger="custom_components.action_control.watchdog"
    ):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"brightness": 200},
            target={"entity_id": "light.kitchen"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert not any(
        record.levelno == logging.INFO
        for record in caplog.records
        if record.name == "custom_components.action_control.watchdog"
    )
