"""Shared base for Action Control entities."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


class ActionControlEntity(Entity):
    """Base entity tying every Action Control entity to one shared device."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Action Control",
            entry_type=DeviceEntryType.SERVICE,
        )
