"""Tests for target resolution and rule-matching filters."""
from __future__ import annotations

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.action_control import matching
from custom_components.action_control.models import Rule
from tests.conftest import make_light_rule


def _make_device(hass) -> dr.DeviceEntry:
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", entry.entry_id)}
    )


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


async def test_resolve_entities_from_device(hass):
    device = _make_device(hass)
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "light", "test", "device_light", suggested_object_id="device_light"
    )
    ent_reg.async_update_entity(entry.entity_id, device_id=device.id)
    hass.states.async_set(entry.entity_id, "off")

    resolved = matching.resolve_target_entities(hass, {"device_id": [device.id]})
    assert entry.entity_id in resolved


async def test_resolve_entities_from_label(hass):
    label = lr.async_get(hass).async_create("watched")
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "switch", "test", "labeled_switch", suggested_object_id="labeled"
    )
    ent_reg.async_update_entity(entry.entity_id, labels={label.label_id})
    hass.states.async_set(entry.entity_id, "off")

    resolved = matching.resolve_target_entities(hass, {"label_id": [label.label_id]})
    assert entry.entity_id in resolved


async def test_resolve_entities_from_label_on_device(hass):
    """A label on the device expands to the device's entities too."""
    label = lr.async_get(hass).async_create("watched-device")
    device = _make_device(hass)
    dr.async_get(hass).async_update_device(device.id, labels={label.label_id})
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "light", "test", "labeled_device_light", suggested_object_id="labeled_device"
    )
    ent_reg.async_update_entity(entry.entity_id, device_id=device.id)
    hass.states.async_set(entry.entity_id, "off")

    resolved = matching.resolve_target_entities(hass, {"label_id": [label.label_id]})
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


async def test_entity_matches_rule_exclude_patterns(hass):
    """The case this exists for: a switch also exposed as a light would
    otherwise be verified twice for a single command."""
    hass.states.async_set("switch.lumiere_salon", "off")
    hass.states.async_set("light.lumiere_salon", "off")
    hass.states.async_set("light.couloir", "off")

    rule = Rule(
        name="r",
        domains=["light", "switch"],
        entity_id_exclude_patterns=["light.lumiere_*", "light.salon_multiprise_*"],
    )
    assert matching.entity_matches_rule(hass, rule, "switch.lumiere_salon")
    assert not matching.entity_matches_rule(hass, rule, "light.lumiere_salon")
    # Anything the patterns don't name stays watched.
    assert matching.entity_matches_rule(hass, rule, "light.couloir")


async def test_exclude_patterns_win_over_the_include_pattern(hass):
    hass.states.async_set("cover.volet_salon", "closed")
    rule = Rule(
        name="r",
        domains=["cover"],
        entity_id_pattern="cover.volet_*",
        entity_id_exclude_patterns=["cover.volet_salon"],
    )
    assert not matching.entity_matches_rule(hass, rule, "cover.volet_salon")


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


async def test_entity_matches_rule_area_filter(hass):
    area = ar.async_get(hass).async_get_or_create("Garage")
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "switch", "test", "garage_switch", suggested_object_id="garage"
    )
    ent_reg.async_update_entity(entry.entity_id, area_id=area.id)
    hass.states.async_set(entry.entity_id, "off")

    rule = Rule(name="r", domains=["switch"], area_ids=[area.id])
    assert matching.entity_matches_rule(hass, rule, entry.entity_id)

    other_rule = Rule(name="r2", domains=["switch"], area_ids=["some-other-area"])
    assert not matching.entity_matches_rule(hass, other_rule, entry.entity_id)


async def test_entity_matches_rule_device_filter(hass):
    device = _make_device(hass)
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get_or_create(
        "switch", "test", "device_switch", suggested_object_id="device_switch"
    )
    ent_reg.async_update_entity(entry.entity_id, device_id=device.id)
    hass.states.async_set(entry.entity_id, "off")

    rule = Rule(name="r", domains=["switch"], device_ids=[device.id])
    assert matching.entity_matches_rule(hass, rule, entry.entity_id)

    other_rule = Rule(name="r2", domains=["switch"], device_ids=["some-other-device"])
    assert not matching.entity_matches_rule(hass, other_rule, entry.entity_id)


async def test_entity_matches_rule_wrong_domain_never_matches(hass):
    hass.states.async_set("switch.x", "off")
    rule = Rule(name="r", domains=["light"])
    assert not matching.entity_matches_rule(hass, rule, "switch.x")


async def test_entity_matches_rule_exclude_by_entity(hass):
    """Picking the duplicate entity by hand, rather than writing a pattern."""
    hass.states.async_set("switch.lumiere_salon", "off")
    hass.states.async_set("light.lumiere_salon", "off")

    rule = Rule(
        name="r",
        domains=["light", "switch"],
        entity_id_exclude=["light.lumiere_salon"],
    )
    assert matching.entity_matches_rule(hass, rule, "switch.lumiere_salon")
    assert not matching.entity_matches_rule(hass, rule, "light.lumiere_salon")


async def test_entity_matches_rule_exclude_by_device(hass):
    """A device exclusion drops every entity that device exposes."""
    excluded = _make_device(hass)
    kept = _make_device(hass)
    ent_reg = er.async_get(hass)
    entries = {}
    for name, device in (("excluded", excluded), ("kept", kept)):
        entry = ent_reg.async_get_or_create(
            "switch", "test", f"{name}_switch", suggested_object_id=name
        )
        ent_reg.async_update_entity(entry.entity_id, device_id=device.id)
        hass.states.async_set(entry.entity_id, "off")
        entries[name] = entry

    rule = Rule(name="r", domains=["switch"], device_id_exclude=[excluded.id])
    assert not matching.entity_matches_rule(hass, rule, entries["excluded"].entity_id)
    assert matching.entity_matches_rule(hass, rule, entries["kept"].entity_id)
