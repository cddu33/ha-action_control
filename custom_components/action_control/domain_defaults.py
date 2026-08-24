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

# Domains that really are on/off. Anything else needs an explicit entry
# below: expecting "on" from a cover is how a rule fails forever.
ON_OFF_DOMAINS = frozenset(
    {
        "light",
        "switch",
        "fan",
        "siren",
        "input_boolean",
        "humidifier",
        "remote",
        "automation",
    }
)

# Acceptable states after a service call. Transitional states are included:
# a cover still travelling reports "opening", which is not a failure.
SERVICE_EXPECTED_STATES: dict[tuple[str, str], frozenset[str]] = {
    ("cover", "open_cover"): frozenset({"open", "opening"}),
    ("cover", "close_cover"): frozenset({"closed", "closing"}),
    ("valve", "open_valve"): frozenset({"open", "opening"}),
    ("valve", "close_valve"): frozenset({"closed", "closing"}),
    ("lock", "lock"): frozenset({"locked", "locking"}),
    ("lock", "unlock"): frozenset({"unlocked", "unlocking"}),
    ("lock", "open"): frozenset({"open", "opening", "unlocked"}),
}

# On/off services, only applied to ON_OFF_DOMAINS.
ON_OFF_SERVICE_STATES: dict[str, frozenset[str]] = {
    "turn_on": frozenset({"on"}),
    "turn_off": frozenset({"off"}),
}

# Domains where "toggle" flips open/closed instead of on/off, as
# {domain: (states meaning open, expected when open, expected when closed)}.
TOGGLE_OPEN_CLOSE_DOMAINS: dict[str, tuple[frozenset[str], frozenset[str], frozenset[str]]] = {
    "cover": (
        frozenset({"open", "opening"}),
        frozenset({"closed", "closing"}),
        frozenset({"open", "opening"}),
    ),
    "valve": (
        frozenset({"open", "opening"}),
        frozenset({"closed", "closing"}),
        frozenset({"open", "opening"}),
    ),
}


def _brightness_pct_to_255(value: Any) -> Any:
    """Convert light.turn_on's brightness_pct to the brightness attribute."""
    try:
        return round(float(value) * 255 / 100)
    except (TypeError, ValueError):
        return value


# Service-data keys an attribute can be read from, when the key isn't the
# attribute's own name or needs converting. First key present in the call wins.
SERVICE_DATA_ATTRIBUTE_SOURCES: dict[
    tuple[str, str], dict[str, tuple[tuple[str, Any], ...]]
] = {
    ("cover", "set_cover_position"): {
        "current_position": (("position", None),),
    },
    ("cover", "set_cover_tilt_position"): {
        "current_tilt_position": (("tilt_position", None),),
    },
    ("valve", "set_valve_position"): {
        "current_position": (("position", None),),
    },
    ("light", "turn_on"): {
        "brightness": (
            ("brightness", None),
            ("brightness_pct", _brightness_pct_to_255),
        ),
        "color_temp_kelvin": (
            ("color_temp_kelvin", None),
            ("kelvin", None),
        ),
    },
}
