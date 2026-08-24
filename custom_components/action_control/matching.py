"""Resolve which entities a service call actually targets, and which
configured rules apply to a given (domain, service, entity) combination.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .models import Rule


def _ensure_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def resolve_target_entities(
    hass: HomeAssistant, service_data: dict[str, Any]
) -> set[str]:
    """Resolve every entity a call_service event's data actually targets.

    Mirrors what the original automations did by hand with
    area_entities/device_entities/label_entities: entity_id/device_id/
    area_id/label_id are expanded to concrete entity ids using the entity
    and device registries directly, so this keeps working across a wide
    range of Home Assistant versions instead of depending on the newer
    generic `helpers.target` resolver.
    """
    entity_ids = set(_ensure_list(service_data.get("entity_id")))
    device_ids = set(_ensure_list(service_data.get("device_id")))
    area_ids = set(_ensure_list(service_data.get("area_id")))
    label_ids = set(_ensure_list(service_data.get("label_id")))

    if not (device_ids or area_ids or label_ids):
        return entity_ids

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    for device_id in device_ids:
        entity_ids.update(
            entry.entity_id for entry in er.async_entries_for_device(ent_reg, device_id)
        )

    for area_id in area_ids:
        entity_ids.update(
            entry.entity_id for entry in er.async_entries_for_area(ent_reg, area_id)
        )
        for device in dr.async_entries_for_area(dev_reg, area_id):
            entity_ids.update(
                entry.entity_id
                for entry in er.async_entries_for_device(ent_reg, device.id)
            )

    for label_id in label_ids:
        entity_ids.update(
            entry.entity_id for entry in er.async_entries_for_label(ent_reg, label_id)
        )
        for device in dr.async_entries_for_label(dev_reg, label_id):
            entity_ids.update(
                entry.entity_id
                for entry in er.async_entries_for_device(ent_reg, device.id)
            )

    return entity_ids


def rule_matches_service(rule: Rule, domain: str, service: str) -> bool:
    """Whether a rule's domain/service filter applies to this call."""
    if domain not in rule.domains:
        return False
    if rule.services and service not in rule.services:
        return False
    return True


def _entity_area_id(hass: HomeAssistant, entity_id: str) -> str | None:
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        if device is not None:
            return device.area_id
    return None


def _entity_label_ids(hass: HomeAssistant, entity_id: str) -> set[str]:
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return set()
    labels = set(entry.labels or [])
    if entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        if device is not None:
            labels |= set(device.labels or [])
    return labels


def _entity_device_id(hass: HomeAssistant, entity_id: str) -> str | None:
    entry = er.async_get(hass).async_get(entity_id)
    return entry.device_id if entry is not None else None


def _entity_name(hass: HomeAssistant, entity_id: str) -> str:
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None and (entry.name or entry.original_name):
        return entry.name or entry.original_name or entity_id
    state = hass.states.get(entity_id)
    return state.name if state is not None else entity_id


def entity_matches_rule(hass: HomeAssistant, rule: Rule, entity_id: str) -> bool:
    """Whether a specific entity satisfies a rule's targeting criteria."""
    if entity_id.split(".", 1)[0] not in rule.domains:
        return False

    if rule.entity_id_pattern and not fnmatchcase(
        entity_id, rule.entity_id_pattern
    ):
        return False

    if rule.name_pattern:
        name = _entity_name(hass, entity_id)
        if not fnmatchcase(name.lower(), rule.name_pattern.lower()):
            return False

    if rule.area_ids and _entity_area_id(hass, entity_id) not in rule.area_ids:
        return False

    if rule.label_ids and not (_entity_label_ids(hass, entity_id) & set(rule.label_ids)):
        return False

    if rule.device_ids and _entity_device_id(hass, entity_id) not in rule.device_ids:
        return False

    return True
