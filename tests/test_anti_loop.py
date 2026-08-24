"""Tests for the Context-based anti-loop mechanism.

This is the single most important regression test in the suite: it proves
that a service call this integration re-issues (a retry, or a replay after
escalation) never causes its own call_service event to spawn a new watchdog
run for the same or any other rule -- the mechanism that fully replaces the
fragile external guard-switch pattern from the original automations.
"""
from __future__ import annotations

from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, Event

from custom_components.action_control.context_registry import SelfIssuedContexts
from custom_components.action_control.coordinator import ActionControlEngine
from tests.conftest import make_light_rule


def test_self_issued_context_is_recognized():
    registry = SelfIssuedContexts()
    ctx = registry.new_context()
    assert registry.is_self_issued(ctx.id)


def test_unknown_context_is_not_self_issued():
    registry = SelfIssuedContexts()
    assert not registry.is_self_issued(Context().id)
    assert not registry.is_self_issued(None)


def test_context_expires_after_ttl(monkeypatch):
    fake_time = {"now": 0.0}
    monkeypatch.setattr(
        "custom_components.action_control.context_registry.time.monotonic",
        lambda: fake_time["now"],
    )
    registry = SelfIssuedContexts(ttl=10)
    ctx = registry.new_context()  # created at fake time 0, expiry = 10

    fake_time["now"] = 11.0
    assert not registry.is_self_issued(ctx.id)


class _FakeEntry:
    def __init__(self, options):
        self.options = options
        self.entry_id = "test_entry"


async def test_self_issued_event_does_not_spawn_a_task(hass):
    rule = make_light_rule()
    entry = _FakeEntry(
        {"rules": {rule.rule_id: rule.to_dict()}, "global": {"enabled": True}}
    )
    engine = ActionControlEngine(hass, entry)

    ctx = engine.contexts.new_context()
    event = Event(
        EVENT_CALL_SERVICE,
        {
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": "light.kitchen", "brightness": 200},
        },
        context=ctx,
    )

    engine._handle_call_service(event)  # noqa: SLF001 - testing the listener directly
    await hass.async_block_till_done()

    assert len(engine._tasks) == 0  # noqa: SLF001


async def test_user_issued_event_does_spawn_a_task(hass, monkeypatch):
    rule = make_light_rule()
    entry = _FakeEntry(
        {"rules": {rule.rule_id: rule.to_dict()}, "global": {"enabled": True}}
    )
    engine = ActionControlEngine(hass, entry)
    hass.states.async_set("light.kitchen", "on", {"brightness": 200, "rgb_color": [1, 1, 1]})

    dispatched = []

    async def _fake_watchdog(*args, **kwargs):
        dispatched.append(args)

    monkeypatch.setattr(
        "custom_components.action_control.coordinator.watchdog.async_run_watchdog",
        _fake_watchdog,
    )

    event = Event(
        EVENT_CALL_SERVICE,
        {
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": "light.kitchen", "brightness": 200},
        },
        context=Context(),
    )

    engine._handle_call_service(event)  # noqa: SLF001
    await hass.async_block_till_done()

    assert dispatched, "a non-self-issued event should trigger rule dispatch"
