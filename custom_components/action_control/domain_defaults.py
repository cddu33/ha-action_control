"""Built-in presets used to prefill the config-flow forms for common domains.

These are only defaults offered by the UI, never hardcoded matching logic:
a rule can target any domain, with any of these fields overridden or left
empty for a fully generic, state-only watchdog.
"""
from __future__ import annotations

from typing import Any

DOMAIN_PRESETS: dict[str, dict[str, Any]] = {
    "light": {
        "attributes_to_check": [
            "brightness",
            "rgb_color",
            "color_temp_kelvin",
            "xy_color",
        ],
        "tolerances": {
            "brightness": 5,
            "rgb_color": 5,
            "color_temp_kelvin": 100,
            "xy_color": 0.01,
        },
        "wait_for_change": False,
    },
    "switch": {
        "attributes_to_check": [],
        "tolerances": {},
        "wait_for_change": False,
    },
    "cover": {
        "attributes_to_check": [],
        "tolerances": {},
        "wait_for_change": True,
        "change_attribute": "current_position",
        "change_timeout": 45.0,
    },
}

# Services whose outcome maps to a simple on/off/open/closed final state.
STATE_SERVICES: dict[str, str] = {
    "turn_on": "on",
    "turn_off": "off",
    "open_cover": "open",
    "close_cover": "closed",
    "lock": "locked",
    "unlock": "unlocked",
}
TOGGLE_SERVICES = {"toggle"}

# For services whose service_data key doesn't share the attribute's name
# (e.g. cover.set_cover_position's "position" maps to the "current_position"
# state attribute), map (domain, service) -> {attribute_name: service_data_key}.
SERVICE_DATA_ATTRIBUTE_ALIASES: dict[tuple[str, str], dict[str, str]] = {
    ("cover", "set_cover_position"): {"current_position": "position"},
    ("cover", "set_cover_tilt_position"): {
        "current_tilt_position": "tilt_position"
    },
}
