"""Tests for ActionControlEngine escalation-cooldown persistence."""
from __future__ import annotations

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import issue_registry as ir

from custom_components.action_control.const import DOMAIN
from custom_components.action_control.coordinator import ActionControlEngine
from tests.conftest import make_cover_rule, make_entry, make_light_rule


async def test_escalation_cooldown_persists_across_reload(hass):
    entry = make_entry(make_cover_rule())
    entry.add_to_hass(hass)
    engine = ActionControlEngine(hass, entry)
    await engine.async_setup()
    rule_id = next(iter(engine.rules))

    engine.arm_escalation_cooldown(rule_id, 300)
    assert not engine.escalation_ready(rule_id)
    await engine._store.async_save(engine._cooldowns_to_save())
    await engine.async_unload()

    reloaded = ActionControlEngine(hass, entry)
    await reloaded.async_setup()
    assert not reloaded.escalation_ready(rule_id)
    await reloaded.async_unload()


async def test_expired_cooldowns_are_not_persisted(hass):
    entry = make_entry(make_cover_rule())
    entry.add_to_hass(hass)
    engine = ActionControlEngine(hass, entry)
    await engine.async_setup()
    rule_id = next(iter(engine.rules))

    engine.arm_escalation_cooldown(rule_id, -1)  # already expired
    saved = engine._cooldowns_to_save()
    assert rule_id not in saved["cooldowns"]
    await engine.async_unload()


async def test_clear_escalation_cooldown_makes_it_ready_again(hass):
    entry = make_entry(make_cover_rule())
    entry.add_to_hass(hass)
    engine = ActionControlEngine(hass, entry)
    await engine.async_setup()
    rule_id = next(iter(engine.rules))

    engine.arm_escalation_cooldown(rule_id, 300)
    assert not engine.escalation_ready(rule_id)

    engine.clear_escalation_cooldown(rule_id)
    assert engine.escalation_ready(rule_id)
    await engine.async_unload()


async def test_stale_area_raises_and_clears_a_repair_issue(hass):
    rule = make_light_rule(area_ids=["missing-area"])
    entry = make_entry(rule)
    entry.add_to_hass(hass)
    engine = ActionControlEngine(hass, entry)
    await engine.async_setup()

    issue_id = f"stale_target_{rule.rule_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    await engine.async_unload()

    # Fix the reference, then a fresh setup must clear the issue.
    area = ar.async_get(hass).async_get_or_create("Kitchen")
    fixed_rule = make_light_rule(rule_id=rule.rule_id, area_ids=[area.id])
    entry2 = make_entry(fixed_rule)
    entry2.add_to_hass(hass)
    engine2 = ActionControlEngine(hass, entry2)
    await engine2.async_setup()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"stale_target_{fixed_rule.rule_id}") is None
    await engine2.async_unload()
