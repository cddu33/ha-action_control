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
    assert result["step_id"] == "rule_features"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "rule_verify"

    # Escalation is off by default, so the wizard ends here.
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
    for _ in range(3):
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


async def test_retry_backoff_and_log_entity_info_are_saved(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Backoff", "domains": ["switch"]}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "rule_features"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"log_entity_info": True}
    )
    assert result["step_id"] == "rule_verify"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"retry_backoff": "exponential"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["retry_backoff"] == "exponential"
    assert rule["log_entity_info"] is True


async def test_retry_backoff_and_log_entity_info_default_when_omitted(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Defaults", "domains": ["switch"]}
    )
    for _ in range(3):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["retry_backoff"] == "constant"
    assert rule["log_entity_info"] is False


async def _start_rule(hass, entry, **targeting):
    """Walk the wizard up to the features step."""
    targeting = {"name": "Rule", "domains": ["switch"], **targeting}
    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], targeting
    )
    return await hass.config_entries.options.async_configure(result["flow_id"], {})


async def test_a_rule_without_any_domain_is_rejected(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "No domain", "domains": []}
    )

    assert result["step_id"] == "add_rule"
    assert result["errors"] == {"domains": "domains_required"}


async def test_movement_mode_requires_an_attribute_to_watch(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Movement", domains=["cover"])
    assert result["step_id"] == "rule_features"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"verification_mode": "movement"}
    )
    assert result["step_id"] == "rule_verify"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"change_attribute": ""}
    )
    assert result["step_id"] == "rule_verify"
    assert result["errors"] == {"change_attribute": "change_attribute_required"}


async def test_movement_mode_is_saved_as_wait_for_change(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Movement", domains=["cover"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"verification_mode": "movement"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"change_attribute": "current_position"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["wait_for_change"] is True
    assert rule["change_attribute"] == "current_position"
    # The wizard key itself never reaches the stored rule.
    assert "verification_mode" not in rule


async def test_delay_mode_does_not_offer_the_movement_fields(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Delay")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"verification_mode": "delay"}
    )
    assert result["step_id"] == "rule_verify"

    keys = set(result["data_schema"].schema)
    assert "check_delay" in keys
    assert "change_attribute" not in keys
    assert "change_timeout" not in keys


async def test_notifications_are_reachable_without_escalation(hass):
    """Notifications moved off the (now conditional) escalation step."""
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Notify only")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"escalation_enabled": False, "notify_persistent": False, "notify_service": "mobile"},
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["escalation_enabled"] is False
    assert rule["notify_persistent"] is False
    assert rule["notify_service"] == "mobile"


async def test_escalation_without_verification_skips_the_check_step(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Escalating")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"escalation_enabled": True}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "rule_escalation"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"escalation_check_enabled": False, "escalation_cooldown": 60}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["escalation_enabled"] is True
    assert rule["escalation_cooldown"] == 60
    assert rule["escalation_check_entity_id"] is None


async def test_escalation_with_verification_asks_for_the_check(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Verified escalation")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"escalation_enabled": True}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"escalation_check_enabled": True}
    )
    assert result["step_id"] == "rule_escalation_check"

    # Both the entity and its expected state are required.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"escalation_check_entity_id": "switch.gateway"}
    )
    assert result["step_id"] == "rule_escalation_check"
    assert result["errors"] == {"escalation_check_state": "escalation_check_required"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"escalation_check_entity_id": "switch.gateway", "escalation_check_state": "on"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["escalation_check_entity_id"] == "switch.gateway"
    assert rule["escalation_check_state"] == "on"


async def test_editing_a_rule_prefills_the_feature_gates(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Verified", domains=["cover"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"verification_mode": "movement", "escalation_enabled": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"change_attribute": "current_position"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"escalation_check_enabled": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"escalation_check_entity_id": "switch.gateway", "escalation_check_state": "on"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    rule_id = next(iter(entry.options[OPT_RULES]))

    result = await _select_menu(hass, entry, "edit_rule_select")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"rule_id": rule_id}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Verified", "domains": ["cover"]}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "rule_features"

    defaults = {key.schema: key.default() for key in result["data_schema"].schema}
    assert defaults["verification_mode"] == "movement"
    assert defaults["escalation_enabled"] is True


async def test_invalid_tolerances_are_rejected(hass):
    entry = await _create_entry(hass)

    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Lights", "domains": ["light"]}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
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


async def test_exclusions_are_skipped_and_cleared_when_unticked(hass):
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "No exclusion", "domains": ["switch"], "exclusions_enabled": False},
    )
    assert result["step_id"] == "add_rule_services"

    for _ in range(3):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["entity_id_exclude"] == []
    assert rule["device_id_exclude"] == []
    assert rule["entity_id_exclude_patterns"] == []
    # The gate is a wizard key, it never reaches the stored rule.
    assert "exclusions_enabled" not in rule


async def test_exclusion_step_saves_entities_devices_and_patterns(hass):
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "With exclusions",
            "domains": ["switch"],
            "exclusions_enabled": True,
        },
    )
    assert result["step_id"] == "rule_exclude"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "entity_id_exclude": ["switch.duplicate"],
            "device_id_exclude": ["some-device-id"],
            "entity_id_exclude_patterns": ["switch.multiprise_*"],
        },
    )
    assert result["step_id"] == "add_rule_services"

    for _ in range(3):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY

    rule = next(iter(entry.options[OPT_RULES].values()))
    assert rule["entity_id_exclude"] == ["switch.duplicate"]
    assert rule["device_id_exclude"] == ["some-device-id"]
    assert rule["entity_id_exclude_patterns"] == ["switch.multiprise_*"]


async def test_the_exclusion_picker_is_limited_to_the_rules_domains(hass):
    """The domain filter is what makes the list readable in the first place."""
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Scoped", "domains": ["switch"], "exclusions_enabled": True},
    )

    schema = result["data_schema"].schema
    selector = next(
        value for key, value in schema.items() if key == "entity_id_exclude"
    )
    assert selector.config["domain"] == ["switch"]


async def test_editing_a_rule_prefills_and_can_drop_its_exclusions(hass):
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Excluding", "domains": ["switch"], "exclusions_enabled": True},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entity_id_exclude": ["switch.duplicate"]}
    )
    for _ in range(3):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    rule_id = next(iter(entry.options[OPT_RULES]))

    # Re-opening the rule tickes the gate back on by itself...
    result = await _select_menu(hass, entry, "edit_rule_select")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"rule_id": rule_id}
    )
    assert result["data_schema"]({"name": "Excluding", "domains": ["switch"]})[
        "exclusions_enabled"
    ]

    # ... and unticking it drops what was excluded, rather than leaving a
    # filter in force that no step of the wizard shows any more.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Excluding", "domains": ["switch"], "exclusions_enabled": False},
    )
    assert result["step_id"] == "add_rule_services"
    for _ in range(3):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})

    assert entry.options[OPT_RULES][rule_id]["entity_id_exclude"] == []


async def test_going_back_returns_to_the_previous_step_keeping_the_input(hass):
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Back and forth")
    assert result["step_id"] == "rule_features"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"verification_mode": "delay"}
    )
    assert result["step_id"] == "rule_verify"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"retries": 7, "go_back": True}
    )
    assert result["step_id"] == "rule_features"

    # Forward again: what was typed on the step we left is still there.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"verification_mode": "delay"}
    )
    assert result["step_id"] == "rule_verify"
    defaults = result["data_schema"]({})
    assert defaults["retries"] == 7


async def test_going_back_skips_the_exclusion_step_when_it_was_unticked(hass):
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Skipping", "domains": ["switch"], "exclusions_enabled": False},
    )
    assert result["step_id"] == "add_rule_services"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"go_back": True}
    )
    assert result["step_id"] == "add_rule"


async def test_going_back_from_the_first_step_returns_to_the_menu(hass):
    """And drops the draft, so the menu's "Add a rule" starts a new rule."""
    entry = await _create_entry(hass)
    result = await _select_menu(hass, entry, "add_rule")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Abandoned", "domains": ["switch"]}
    )
    assert result["step_id"] == "add_rule_services"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"go_back": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"go_back": True}
    )
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_rule"}
    )
    assert result["data_schema"]({"domains": []})["name"] == ""
    assert not entry.options[OPT_RULES]


async def test_going_back_from_the_last_conditional_step(hass):
    """Both escalation gates are on, so the way back runs through them."""
    entry = await _create_entry(hass)
    result = await _start_rule(hass, entry, name="Deep")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"escalation_enabled": True}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"escalation_check_enabled": True}
    )
    assert result["step_id"] == "rule_escalation_check"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"go_back": True}
    )
    assert result["step_id"] == "rule_escalation"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"go_back": True}
    )
    assert result["step_id"] == "rule_verify"
