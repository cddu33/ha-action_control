"""Tests for config entry diagnostics."""
from __future__ import annotations

from custom_components.action_control.const import OPT_RULES
from custom_components.action_control.diagnostics import async_get_config_entry_diagnostics
from tests.conftest import make_entry, make_light_rule


async def test_diagnostics_includes_rules_and_redacts_secrets(hass):
    rule = make_light_rule(
        escalation_action=[{"action": "notify.mobile", "data": {"token": "secret123"}}]
    )
    entry = make_entry(rule)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert rule.rule_id in diagnostics["options"][OPT_RULES]
    assert "rule_status" in diagnostics
    assert "secret123" not in str(diagnostics)
