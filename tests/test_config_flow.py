"""Tests for the config flow (single instance) and options flow (rule CRUD)."""
from __future__ import annotations

import json
import pathlib
import re

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.action_control.const import (
    DEFAULT_LOG_ENTITY_INFO,
    DEFAULT_RETRY_BACKOFF,
    DOMAIN,
    OPT_GLOBAL,
    OPT_RULES,
)


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


async def _submit(hass, result, data=None):
    """Submit the form the flow is showing, letting defaults fill the rest."""
    return await hass.config_entries.options.async_configure(
        result["flow_id"], data or {}
    )


async def _defaults(hass, result, times=1):
    """Accept the next `times` forms as they come."""
    for _ in range(times):
        result = await _submit(hass, result)
    return result


async def _select_menu(hass, entry, option: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    return await _submit(hass, result, {"next_step_id": option})


async def _start_rule(hass, entry, **targeting):
    """Walk the wizard up to the features step."""
    targeting = {"name": "Rule", "domains": ["switch"], **targeting}
    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(hass, result, targeting)
    return await _submit(hass, result)


async def _edit_rule(hass, entry, rule_id):
    """Open an existing rule, which lands straight on its menu."""
    result = await _select_menu(hass, entry, "edit_rule_select")
    return await _submit(hass, result, {"rule_id": rule_id})


async def _pick_section(hass, result, section: str):
    """Open one section from the rule menu."""
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "rule_menu"
    return await _submit(hass, result, {"next_step_id": section})


def _rule_menu_strings(key: str) -> dict[str, str]:
    strings = json.loads(
        (
            pathlib.Path(__file__).parent.parent
            / "custom_components/action_control/strings.json"
        ).read_text()
    )
    return strings["options"]["step"]["rule_menu"][key]


def _menu_labels() -> dict[str, str]:
    return _rule_menu_strings("menu_options")


async def _save_rule(hass, result):
    """Every walk through the wizard ends on the rule menu, not on a save."""
    result = await _pick_section(hass, result, "rule_save")
    assert result["type"] == FlowResultType.CREATE_ENTRY
    return result


async def test_add_edit_delete_rule_cycle(hass):
    entry = await _create_entry(hass)

    # --- add a rule ---
    result = await _select_menu(hass, entry, "new_rule")
    assert result["step_id"] == "add_rule"

    result = await _submit(hass, result, {"name": "Test rule", "domains": ["switch"]})
    assert result["step_id"] == "add_rule_services"

    result = await _submit(hass, result)
    assert result["step_id"] == "rule_features"

    result = await _submit(hass, result)
    assert result["step_id"] == "rule_verify"

    # Escalation is off by default, so the guided pass ends on the rule menu.
    result = await _submit(hass, result)
    await _save_rule(hass, result)

    rules = entry.options[OPT_RULES]
    assert len(rules) == 1
    rule_id, rule_data = next(iter(rules.items()))
    assert rule_data["name"] == "Test rule"
    assert rule_data["domains"] == ["switch"]

    # --- edit that rule ---
    # Editing lands on the menu: one section to change, then save.
    result = await _edit_rule(hass, entry, rule_id)
    result = await _pick_section(hass, result, "add_rule")
    assert result["step_id"] == "add_rule"

    result = await _submit(hass, result, {"name": "Renamed rule", "domains": ["switch"]})
    await _save_rule(hass, result)

    rules = entry.options[OPT_RULES]
    assert len(rules) == 1  # edited in place, not duplicated
    assert rules[rule_id]["name"] == "Renamed rule"

    # --- delete that rule ---
    result = await _select_menu(hass, entry, "delete_rule_select")
    result = await _submit(hass, result, {"rule_id": rule_id})
    assert result["step_id"] == "delete_rule_confirm"

    result = await _submit(hass, result, {"confirm": True})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_RULES] == {}


async def test_services_step_offers_the_chosen_domains_registered_services(hass):
    hass.services.async_register("light", "turn_on", lambda call: None)
    hass.services.async_register("light", "turn_off", lambda call: None)

    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(hass, result, {"name": "Lights", "domains": ["light"]})
    assert result["step_id"] == "add_rule_services"

    options = result["data_schema"].schema["services"].config["options"]
    assert set(options) == {"turn_on", "turn_off"}


async def test_new_rule_starts_from_the_global_retry_defaults(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "global_settings")
    await _submit(hass, result, {"enabled": True, "default_retries": 5, "default_retry_delay": 7})
    await hass.async_block_till_done()

    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(hass, result, {"name": "Defaults", "domains": ["switch"]})
    result = await _defaults(hass, result, 3)
    await _save_rule(hass, result)

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["retries"] == 5
    assert rule["retry_delay"] == 7


async def test_retry_backoff_and_log_entity_info_are_saved(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(hass, result, {"name": "Backoff", "domains": ["switch"]})
    result = await _submit(hass, result)
    assert result["step_id"] == "rule_features"

    result = await _submit(hass, result, {"log_entity_info": True})
    assert result["step_id"] == "rule_verify"

    result = await _submit(hass, result, {"retry_backoff": "exponential"})
    await _save_rule(hass, result)

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["retry_backoff"] == "exponential"
    assert rule["log_entity_info"] is True


async def test_the_wizard_falls_back_to_the_module_defaults(hass):
    """Compared against the constants, not literals: what the constants are
    worth is test_models\'s job, what the wizard reads is this one\'s."""
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(hass, result, {"name": "Defaults", "domains": ["switch"]})
    result = await _defaults(hass, result, 3)
    await _save_rule(hass, result)

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["retry_backoff"] == DEFAULT_RETRY_BACKOFF
    assert rule["log_entity_info"] is DEFAULT_LOG_ENTITY_INFO


async def test_a_rule_without_any_domain_is_rejected(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(hass, result, {"name": "No domain", "domains": []})

    assert result["step_id"] == "add_rule"
    assert result["errors"] == {"domains": "domains_required"}


async def test_movement_mode_requires_an_attribute_to_watch(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Movement", domains=["cover"])
    assert result["step_id"] == "rule_features"

    result = await _submit(hass, result, {"verification_mode": "movement"})
    assert result["step_id"] == "rule_verify"

    result = await _submit(hass, result, {"change_attribute": ""})
    assert result["step_id"] == "rule_verify"
    assert result["errors"] == {"change_attribute": "change_attribute_required"}


async def test_movement_mode_is_saved_as_wait_for_change(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Movement", domains=["cover"])
    result = await _submit(hass, result, {"verification_mode": "movement"})
    result = await _submit(hass, result, {"change_attribute": "current_position"})
    await _save_rule(hass, result)

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["wait_for_change"] is True
    assert rule["change_attribute"] == "current_position"
    # The wizard key itself never reaches the stored rule.
    assert "verification_mode" not in rule


async def test_delay_mode_does_not_offer_the_movement_fields(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Delay")
    result = await _submit(hass, result, {"verification_mode": "delay"})
    assert result["step_id"] == "rule_verify"

    keys = set(result["data_schema"].schema)
    assert "check_delay" in keys
    assert "change_attribute" not in keys
    assert "change_timeout" not in keys


async def test_notifications_are_reachable_without_escalation(hass):
    """Notifications moved off the (now conditional) escalation step."""
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Notify only")

    result = await _submit(
        hass,
        result,
        {
            "escalation_enabled": False,
            "notify_persistent": False,
            "notify_service": "mobile",
        },
    )
    result = await _submit(hass, result)
    await _save_rule(hass, result)

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["escalation_enabled"] is False
    assert rule["notify_persistent"] is False
    assert rule["notify_service"] == "mobile"


async def test_escalation_without_verification_skips_the_check_step(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Escalating")

    result = await _submit(hass, result, {"escalation_enabled": True})
    result = await _submit(hass, result)
    assert result["step_id"] == "rule_escalation"

    result = await _submit(
        hass, result, {"escalation_check_enabled": False, "escalation_cooldown": 60}
    )
    await _save_rule(hass, result)

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["escalation_enabled"] is True
    assert rule["escalation_cooldown"] == 60
    assert rule["escalation_check_entity_id"] is None


async def test_escalation_with_verification_asks_for_the_check(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Verified escalation")

    result = await _submit(hass, result, {"escalation_enabled": True})
    result = await _submit(hass, result)
    result = await _submit(hass, result, {"escalation_check_enabled": True})
    assert result["step_id"] == "rule_escalation_check"

    # Both the entity and its expected state are required.
    result = await _submit(hass, result, {"escalation_check_entity_id": "switch.gateway"})
    assert result["step_id"] == "rule_escalation_check"
    assert result["errors"] == {"escalation_check_state": "escalation_check_required"}

    result = await _submit(
        hass,
        result,
        {
            "escalation_check_entity_id": "switch.gateway",
            "escalation_check_state": "on",
        },
    )
    await _save_rule(hass, result)

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["escalation_check_entity_id"] == "switch.gateway"
    assert rule["escalation_check_state"] == "on"


async def test_editing_a_rule_prefills_the_feature_gates(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Verified", domains=["cover"])
    result = await _submit(
        hass, result, {"verification_mode": "movement", "escalation_enabled": True}
    )
    result = await _submit(hass, result, {"change_attribute": "current_position"})
    result = await _submit(hass, result, {"escalation_check_enabled": True})
    result = await _submit(
        hass,
        result,
        {
            "escalation_check_entity_id": "switch.gateway",
            "escalation_check_state": "on",
        },
    )
    await _save_rule(hass, result)
    rule_id = next(iter(entry.options[OPT_RULES]))

    result = await _edit_rule(hass, entry, rule_id)
    result = await _pick_section(hass, result, "rule_features")
    assert result["step_id"] == "rule_features"

    defaults = {key.schema: key.default() for key in result["data_schema"].schema}
    assert defaults["verification_mode"] == "movement"
    assert defaults["escalation_enabled"] is True


async def test_invalid_tolerances_are_rejected(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(hass, result, {"name": "Lights", "domains": ["light"]})
    result = await _submit(hass, result)
    result = await _submit(hass, result)
    result = await _submit(hass, result, {"tolerances": "brightness=5"})

    assert result["step_id"] == "rule_verify"
    assert result["errors"] == {"tolerances": "invalid_tolerances"}


async def test_disabled_rule_and_deletion_removes_its_sensor(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(
        hass, result, {"name": "Paused", "domains": ["switch"], "enabled": False}
    )
    result = await _defaults(hass, result, 3)
    await _save_rule(hass, result)
    await hass.async_block_till_done()

    rule_id, rule_data = next(iter(entry.options[OPT_RULES].items()))
    assert rule_data["enabled"] is False

    ent_reg = er.async_get(hass)
    unique_id = f"{entry.entry_id}_{rule_id}"
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id) is not None

    result = await _select_menu(hass, entry, "delete_rule_select")
    result = await _submit(hass, result, {"rule_id": rule_id})
    result = await _submit(hass, result, {"confirm": True})
    await hass.async_block_till_done()

    assert entry.options[OPT_RULES] == {}
    assert ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id) is None


async def test_global_settings(hass):
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "global_settings")
    assert result["step_id"] == "global_settings"

    result = await _submit(
        hass, result, {"enabled": False, "default_retries": 3, "default_retry_delay": 5}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_GLOBAL]["enabled"] is False
    assert entry.options[OPT_GLOBAL]["default_retries"] == 3
    assert entry.options[OPT_GLOBAL]["default_retry_delay"] == 5


async def test_exclusions_are_skipped_and_cleared_when_unticked(hass):
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(
        hass, result, {"name": "No exclusion", "domains": ["switch"], "exclusions_enabled": False}
    )
    assert result["step_id"] == "add_rule_services"

    result = await _defaults(hass, result, 3)
    await _save_rule(hass, result)

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["entity_id_exclude"] == []
    assert rule["device_id_exclude"] == []
    assert rule["entity_id_exclude_patterns"] == []
    # The gate is a wizard key, it never reaches the stored rule.
    assert "exclusions_enabled" not in rule


async def test_exclusion_step_saves_entities_devices_and_patterns(hass):
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(
        hass,
        result,
        {
            "name": "With exclusions",
            "domains": ["switch"],
            "exclusions_enabled": True,
        },
    )
    assert result["step_id"] == "rule_exclude"

    result = await _submit(
        hass,
        result,
        {
            "entity_id_exclude": ["switch.duplicate"],
            "device_id_exclude": ["some-device-id"],
            "entity_id_exclude_patterns": ["switch.multiprise_*"],
        },
    )
    assert result["step_id"] == "add_rule_services"

    result = await _defaults(hass, result, 3)
    await _save_rule(hass, result)

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["entity_id_exclude"] == ["switch.duplicate"]
    assert rule["device_id_exclude"] == ["some-device-id"]
    assert rule["entity_id_exclude_patterns"] == ["switch.multiprise_*"]


async def test_the_exclusion_picker_is_limited_to_the_rules_domains(hass):
    """The domain filter is what makes the list readable in the first place."""
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(
        hass, result, {"name": "Scoped", "domains": ["switch"], "exclusions_enabled": True}
    )

    schema = result["data_schema"].schema
    selector = next(
        value for key, value in schema.items() if key == "entity_id_exclude"
    )
    assert selector.config["domain"] == ["switch"]


async def test_editing_a_rule_prefills_and_can_drop_its_exclusions(hass):
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(
        hass, result, {"name": "Excluding", "domains": ["switch"], "exclusions_enabled": True}
    )
    result = await _submit(hass, result, {"entity_id_exclude": ["switch.duplicate"]})
    result = await _defaults(hass, result, 3)
    await _save_rule(hass, result)
    rule_id = next(iter(entry.options[OPT_RULES]))

    # Re-opening the rule lists the exclusion section and tickes the gate back
    # on by itself...
    result = await _edit_rule(hass, entry, rule_id)
    assert "rule_exclude" in result["menu_options"]
    result = await _pick_section(hass, result, "add_rule")
    assert result["data_schema"]({"name": "Excluding", "domains": ["switch"]})[
        "exclusions_enabled"
    ]

    # ... and unticking it drops what was excluded, rather than leaving a
    # filter in force that nothing shows any more.
    result = await _submit(
        hass, result, {"name": "Excluding", "domains": ["switch"], "exclusions_enabled": False}
    )
    assert "rule_exclude" not in result["menu_options"]
    await _save_rule(hass, result)

    assert entry.options[OPT_RULES][rule_id]["entity_id_exclude"] == []


async def test_the_menu_lists_only_the_sections_the_rule_uses(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Plain")
    result = await _submit(hass, result, {"escalation_enabled": False})
    result = await _submit(hass, result)

    assert result["step_id"] == "rule_menu"
    assert result["menu_options"] == [
        "add_rule",
        "add_rule_services",
        "rule_features",
        "rule_verify",
        "rule_save",
    ]


async def test_every_menu_option_has_a_label(hass):
    """A menu option with no translation shows up as its raw step id."""
    entry = await _create_entry(hass)

    # A rule using every section there is, so the menu lists them all.
    result = await _select_menu(hass, entry, "new_rule")
    result = await _submit(
        hass, result, {"name": "Everything", "domains": ["cover"], "exclusions_enabled": True}
    )
    assert result["step_id"] == "rule_exclude"
    result = await _submit(hass, result)
    result = await _submit(hass, result)
    result = await _submit(
        hass, result, {"escalation_enabled": True, "verification_mode": "movement"}
    )
    result = await _submit(hass, result, {"change_attribute": "current_position"})
    result = await _submit(hass, result, {"escalation_check_enabled": True})
    result = await _submit(
        hass,
        result,
        {
            "escalation_check_entity_id": "switch.gateway",
            "escalation_check_state": "on",
        },
    )

    assert set(result["menu_options"]) == set(_menu_labels())


async def test_every_placeholder_the_menu_references_is_supplied(hass):
    """A placeholder the summaries forget renders as a raw "{name}" under the
    button, which nothing else in the suite would notice."""
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Summarised", domains=["switch"])
    result = await _defaults(hass, result, 2)

    placeholders = result["description_placeholders"]
    for text in _rule_menu_strings("menu_option_descriptions").values():
        for field in re.findall(r"\{(\w+)\}", text):
            assert field in placeholders


async def test_a_section_opened_from_the_menu_comes_back_to_it(hass):
    """What replaces going back: fix one section, land on the menu again."""
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Typo")
    result = await _submit(hass, result)
    result = await _submit(hass, result)
    assert result["step_id"] == "rule_menu"

    result = await _pick_section(hass, result, "add_rule")
    assert result["step_id"] == "add_rule"
    result = await _submit(hass, result, {"name": "Fixed", "domains": ["switch"]})
    assert result["step_id"] == "rule_menu"
    await _save_rule(hass, result)

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["name"] == "Fixed"


async def test_nothing_is_written_until_the_rule_is_saved(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Unsaved")
    result = await _submit(hass, result)
    result = await _submit(hass, result)

    assert result["step_id"] == "rule_menu"
    assert entry.options[OPT_RULES] == {}
