"""Tests for the config flow (single instance) and options flow (rule CRUD)."""
from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

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
    assert result["step_id"] == "add_rule_services"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
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


async def test_services_step_offers_the_chosen_domains_registered_services(hass):
    hass.services.async_register("light", "turn_on", lambda call: None)
    hass.services.async_register("light", "turn_off", lambda call: None)

    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Lights", "domains": ["light"]}
    )
    assert result["step_id"] == "add_rule_services"

    options = result["data_schema"].schema["services"].config["options"]
    assert set(options) == {"turn_on", "turn_off"}


async def test_new_rule_starts_from_the_global_retry_defaults(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "global_settings")
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"enabled": True, "default_retries": 5, "default_retry_delay": 7},
    )
    await hass.async_block_till_done()

    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Defaults", "domains": ["switch"]}
    )
    for _ in range(3):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["retries"] == 5
    assert rule["retry_delay"] == 7


async def test_invalid_tolerances_are_rejected(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Lights", "domains": ["light"]}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tolerances": "brightness=5"}
    )

    assert result["step_id"] == "rule_verify"
    assert result["errors"] == {"tolerances": "invalid_tolerances"}


async def test_disabled_rule_and_deletion_removes_its_sensor(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Paused", "domains": ["switch"], "enabled": False}
    )
    for _ in range(3):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    rule_id, rule_data = next(iter(entry.options[OPT_RULES].items()))
    assert rule_data["enabled"] is False

    ent_reg = er.async_get(hass)
    unique_id = f"{entry.entry_id}_{rule_id}"
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id) is not None

    result = await _select_menu(hass, entry, "delete_rule_select")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"rule_id": rule_id}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    await hass.async_block_till_done()

    assert entry.options[OPT_RULES] == {}
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id) is None


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
