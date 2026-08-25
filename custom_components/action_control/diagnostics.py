"""Diagnostics support for Action Control."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import DATA_ENGINE, DOMAIN

TO_REDACT = {"token", "password", "api_key", "webhook_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    engine = hass.data[DOMAIN][entry.entry_id][DATA_ENGINE]
    return {
        "options": async_redact_data(entry.options, TO_REDACT),
        "rule_status": async_redact_data(
            {rule_id: asdict(status) for rule_id, status in engine.rule_status.items()},
            TO_REDACT,
        ),
    }
