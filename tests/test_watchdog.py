"""Integration-style tests: call_service event -> verify/retry/notify flow."""
from __future__ import annotations

import asyncio
import logging

import pytest
from homeassistant.core import ServiceCall

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


# ---- retry backoff ----


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
