"""Tests for Rule.to_dict/from_dict round-tripping."""
from __future__ import annotations

from custom_components.action_control.models import Rule


async def test_round_trip_preserves_every_field(hass):
    rule = Rule(
        name="Full rule",
        domains=["cover"],
        services=["open_cover"],
        entity_id_pattern="cover.volet_*",
        name_pattern="Volet*",
        area_ids=["area1"],
        label_ids=["label1"],
        device_ids=["device1"],
        attributes_to_check=["current_position"],
        tolerances={"current_position": 5},
        retries=3,
        retry_delay=10,
        retry_backoff="linear",
        log_entity_info=True,
        wait_for_change=True,
        change_attribute="current_position",
        change_timeout=60,
        escalation_enabled=True,
        escalation_action=[{"action": "script.restart", "data": {}}],
        escalation_cooldown=600,
        escalation_replay_delay=30,
        escalation_check_entity_id="switch.restart",
        escalation_check_state="on",
        escalation_check_delay=5,
        notify_persistent=False,
        notify_service="mobile_app",
    )

    restored = Rule.from_dict(rule.to_dict())

    assert restored == rule


async def test_round_trip_fills_in_defaults_for_missing_keys(hass):
    restored = Rule.from_dict({"name": "Minimal"})

    assert restored.name == "Minimal"
    assert restored.domains == []
    assert restored.retries == 2
    assert restored.retry_backoff == "constant"
    assert restored.escalation_check_entity_id is None


async def test_retries_is_stored_as_an_int(hass):
    """The number selector yields a float, which otherwise shows up as
    "retry 1/4.0" in the logs."""
    restored = Rule.from_dict({"name": "Floaty", "retries": 4.0})

    assert restored.retries == 4
    assert isinstance(restored.retries, int)


async def test_to_dict_does_not_alias_the_source_lists(hass):
    rule = Rule(name="r", domains=["light"], area_ids=["a1"])
    data = rule.to_dict()
    data["domains"].append("switch")
    data["area_ids"].append("a2")

    assert rule.domains == ["light"]
    assert rule.area_ids == ["a1"]
