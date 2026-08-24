"""Tests for the config flow (single instance) and options flow (rule CRUD)."""
from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType

from custom_components.action_control.const import DOMAIN, OPT_RULES


async def test_single_instance_enforced(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def _create_entry(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    entry_id = result["result"].entry_id
    return hass.config_entries.async_get_entry(entry_id)


async def _select_menu(hass, entry, option: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": option}
    )


async def test_add_edit_delete_rule_cycle(hass):
    entry = await _create_entry(hass)

    # --- add a rule ---
    result = await _select_menu(hass, entry, "add_rule")
    assert result["step_id"] == "add_rule"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Test rule", "domains": ["switch"]}
    )
    assert result["step_id"] == "rule_verify"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "rule_escalation"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rules = entry.options[OPT_RULES]
    assert len(rules) == 1
    rule_id, rule_data = next(iter(rules.items()))
    assert rule_data["name"] == "Test rule"
    assert rule_data["domains"] == ["switch"]

    # --- edit that rule ---
    result = await _select_menu(hass, entry, "edit_rule_select")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"rule_id": rule_id}
    )
    assert result["step_id"] == "add_rule"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Renamed rule", "domains": ["switch"]}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rules = entry.options[OPT_RULES]
    assert len(rules) == 1  # edited in place, not duplicated
    assert rules[rule_id]["name"] == "Renamed rule"

    # --- delete that rule ---
    result = await _select_menu(hass, entry, "delete_rule_select")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"rule_id": rule_id}
    )
    assert result["step_id"] == "delete_rule_confirm"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_RULES] == {}


async def test_global_settings(hass):
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "global_settings")
    assert result["step_id"] == "global_settings"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"enabled": False, "default_retries": 3, "default_retry_delay": 5},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["global"]["enabled"] is False
    assert entry.options["global"]["default_retries"] == 3
