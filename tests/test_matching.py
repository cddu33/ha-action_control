"""Tests for target resolution and rule-matching filters."""
from __future__ import annotations

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr

from custom_components.action_control import matching
from custom_components.action_control.models import Rule
from tests.conftest import make_light_rule


async def test_resolve_entities_from_area(hass):
    area = ar.async_get(hass).async_get_or_create("Kitchen")
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "light", "test", "kitchen_light", suggested_object_id="kitchen"
    )
    ent_reg.async_update_entity(entry.entity_id, area_id=area.id)
    hass.states.async_set(entry.entity_id, "off")

    resolved = matching.resolve_target_entities(hass, {"area_id": [area.id]})
    assert entry.entity_id in resolved


async def test_resolve_entities_from_floor(hass):
    floor = fr.async_get(hass).async_create("Upstairs")
    area_reg = ar.async_get(hass)
    area = area_reg.async_get_or_create("Bedroom")
    area_reg.async_update(area.id, floor_id=floor.floor_id)
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "light", "test", "bedroom_light", suggested_object_id="bedroom"
    )
    ent_reg.async_update_entity(entry.entity_id, area_id=area.id)
    hass.states.async_set(entry.entity_id, "off")

    resolved = matching.resolve_target_entities(hass, {"floor_id": [floor.floor_id]})
    assert entry.entity_id in resolved


async def test_disabled_entities_are_never_resolved(hass):
    """A disabled entity has no state, so watching it would fail forever."""
    area = ar.async_get(hass).async_get_or_create("Office")
    ent_reg = er.async_get(hass)
    enabled = ent_reg.async_get_or_create(
        "light", "test", "office_light", suggested_object_id="office"
    )
    disabled = ent_reg.async_get_or_create(
        "light",
        "test",
        "spare_light",
        suggested_object_id="spare",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    for entry in (enabled, disabled):
        ent_reg.async_update_entity(entry.entity_id, area_id=area.id)

    resolved = matching.resolve_target_entities(hass, {"area_id": [area.id]})
    assert enabled.entity_id in resolved
    assert disabled.entity_id not in resolved


async def test_rule_matches_service_domain_and_service():
    rule = make_light_rule(services=["turn_on"])
    assert matching.rule_matches_service(rule, "light", "turn_on")
    assert not matching.rule_matches_service(rule, "light", "turn_off")
    assert not matching.rule_matches_service(rule, "switch", "turn_on")


async def test_rule_matches_service_empty_services_is_wildcard():
    rule = make_light_rule(services=[])
    assert matching.rule_matches_service(rule, "light", "anything")


async def test_entity_matches_rule_entity_id_pattern(hass):
    hass.states.async_set("cover.volet_salon", "closed", {"current_position": 0})
    hass.states.async_set("cover.autre", "closed", {"current_position": 0})
    rule = Rule(name="covers", domains=["cover"], entity_id_pattern="cover.volet_*")
    assert matching.entity_matches_rule(hass, rule, "cover.volet_salon")
    assert not matching.entity_matches_rule(hass, rule, "cover.autre")


async def test_entity_matches_rule_name_pattern(hass):
    hass.states.async_set("light.x", "on", {"friendly_name": "Salon Lamp"})
    rule = Rule(name="r", domains=["light"], name_pattern="salon*")
    assert matching.entity_matches_rule(hass, rule, "light.x")


async def test_entity_matches_rule_label_filter(hass):
    label = lr.async_get(hass).async_create("watched")
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "switch", "test", "labeled_switch", suggested_object_id="labeled"
    )
    ent_reg.async_update_entity(entry.entity_id, labels={label.label_id})
    hass.states.async_set(entry.entity_id, "off")

    rule = Rule(name="r", domains=["switch"], label_ids=[label.label_id])
    assert matching.entity_matches_rule(hass, rule, entry.entity_id)

    other_rule = Rule(name="r2", domains=["switch"], label_ids=["some-other-label"])
    assert not matching.entity_matches_rule(hass, other_rule, entry.entity_id)


async def test_entity_matches_rule_wrong_domain_never_matches(hass):
    hass.states.async_set("switch.x", "off")
    rule = Rule(name="r", domains=["light"])
    assert not matching.entity_matches_rule(hass, rule, "switch.x")
