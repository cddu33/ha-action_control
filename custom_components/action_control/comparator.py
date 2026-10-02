"""Generic, tolerance-aware comparison of expected vs. actual state/attributes.

Scalars are compared with an absolute-value tolerance, list/tuple values
(rgb_color, xy_color, ...) element-wise with that same tolerance, anything
else (strings, booleans, None) by exact match. The expected state is a set,
not a single value, so transitional states like "opening" still pass.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import State

from .domain_defaults import (
    ON_OFF_DOMAINS,
    ON_OFF_SERVICE_STATES,
    SERVICE_DATA_ATTRIBUTE_SOURCES,
    SERVICE_EXPECTED_STATES,
    TOGGLE_OPEN_CLOSE_DOMAINS,
)
from .models import ComparisonResult, Mismatch


def _as_state_set(expected_state: Any) -> frozenset[str] | None:
    """Normalize an expected state (str, iterable or None) to a set."""
    if expected_state is None:
        return None
    if isinstance(expected_state, str):
        return frozenset({expected_state})
    return frozenset(expected_state)


def format_expected_state(expected_state: Any) -> str | None:
    """Human-readable form of an expected state, for logs and notifications."""
    states = _as_state_set(expected_state)
    if states is None:
        return None
    return " | ".join(sorted(states))


def expected_states_for(
    domain: str, service: str, current_state: State | None
) -> frozenset[str] | None:
    """Acceptable states after `domain.service`, or None if it implies none."""
    if service == "toggle":
        if domain in TOGGLE_OPEN_CLOSE_DOMAINS:
            open_states, when_open, when_closed = TOGGLE_OPEN_CLOSE_DOMAINS[domain]
            is_open = current_state is not None and current_state.state in open_states
            return when_open if is_open else when_closed
        if domain in ON_OFF_DOMAINS:
            was_on = current_state is not None and current_state.state == "on"
            return frozenset({"off"}) if was_on else frozenset({"on"})
        return None

    expected = SERVICE_EXPECTED_STATES.get((domain, service))
    if expected is not None:
        return expected
    if domain in ON_OFF_DOMAINS:
        return ON_OFF_SERVICE_STATES.get(service)
    return None


def compute_expected(
    domain: str,
    service: str,
    service_data: dict[str, Any],
    attributes_to_check: list[str],
    current_state: State | None,
) -> tuple[frozenset[str] | None, dict[str, Any]]:
    """Derive the expected state(s) and attributes for a just-issued call."""
    expected_state = expected_states_for(domain, service, current_state)

    sources = SERVICE_DATA_ATTRIBUTE_SOURCES.get((domain, service), {})
    expected_attributes: dict[str, Any] = {}
    for attr in attributes_to_check:
        for data_key, convert in sources.get(attr, ((attr, None),)):
            if data_key in service_data:
                value = service_data[data_key]
                expected_attributes[attr] = convert(value) if convert else value
                break

    if domain == "light" and expected_state == frozenset({"on"}):
        expected_state, expected_attributes = _adjust_light_on(
            service_data, expected_attributes, current_state
        )
    # A light, switch or fan that is off has no brightness or color to
    # compare: a toggle that turns it off with brightness in its data must not
    # then expect that brightness.
    if expected_state == frozenset({"off"}):
        expected_attributes = {}

    return expected_state, expected_attributes


_LIGHT_COLOR_MODES = frozenset({"hs", "xy", "rgb", "rgbw", "rgbww"})
_LIGHT_COLOR_ATTRIBUTES = ("rgb_color", "xy_color", "hs_color")


def _adjust_light_on(
    service_data: dict[str, Any],
    expected_attributes: dict[str, Any],
    current_state: State | None,
) -> tuple[frozenset[str], dict[str, Any]]:
    """Expect what Home Assistant will really make of a light turn-on.

    The light component turns a light off when asked for brightness 0, drops
    what the light cannot do, and emulates a color temperature the light has
    no mode for -- which then reports no color_temp_kelvin at all. Expecting
    the raw service data in those cases is a failure every single time.
    """
    for key in ("brightness", "brightness_pct"):
        if key in service_data and _is_zero(service_data[key]):
            return frozenset({"off"}), {}

    attributes = current_state.attributes if current_state is not None else {}
    modes = attributes.get("supported_color_modes")
    if not modes:
        # Capabilities unknown (no state yet, or a legacy light): keep
        # comparing what was asked for.
        return frozenset({"on"}), expected_attributes

    modes = set(modes)
    expected = dict(expected_attributes)
    if modes <= {"onoff"}:
        expected.pop("brightness", None)
    if "color_temp" not in modes:
        expected.pop("color_temp_kelvin", None)
    elif isinstance(expected.get("color_temp_kelvin"), (int, float)):
        # A light only goes as warm or as cold as it can; asking beyond its
        # range lands on the nearest end of it.
        low = attributes.get("min_color_temp_kelvin")
        high = attributes.get("max_color_temp_kelvin")
        kelvin = expected["color_temp_kelvin"]
        if isinstance(low, (int, float)):
            kelvin = max(kelvin, low)
        if isinstance(high, (int, float)):
            kelvin = min(kelvin, high)
        expected["color_temp_kelvin"] = kelvin
    if not modes & _LIGHT_COLOR_MODES:
        for attr in _LIGHT_COLOR_ATTRIBUTES:
            expected.pop(attr, None)
    return frozenset({"on"}), expected


def _is_zero(value: Any) -> bool:
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False


def _normalized_rgb(value: Any) -> Any:
    """Scale an RGB triple so its brightest channel is 255.

    Lights driven in hs or xy report rgb_color at full intensity -- brightness
    is a separate attribute -- so [200, 0, 0] comes back as [255, 0, 0]. The
    hue is what was asked for; comparing the raw values would call it a
    failure.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return value
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return value
    peak = max(value)
    if peak <= 0:
        return value
    return [v * 255 / peak for v in value]


def _values_match(expected: Any, actual: Any, tolerance: float) -> bool:
    if expected is None:
        return True
    if actual is None:
        return False
    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        if len(expected) != len(actual):
            return False
        return all(
            _values_match(exp_item, act_item, tolerance)
            for exp_item, act_item in zip(expected, actual, strict=True)
        )
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return False
        return abs(float(expected) - float(actual)) <= tolerance
    return expected == actual


def compare(
    expected_state: Any,
    expected_attributes: dict[str, Any],
    tolerances: dict[str, float],
    actual: State | None,
) -> ComparisonResult:
    """Compare the current state/attributes of an entity against expectations."""
    mismatches: list[Mismatch] = []
    expected_states = _as_state_set(expected_state)
    expected_label = format_expected_state(expected_states)

    if actual is None:
        mismatches.append(Mismatch("state", expected_label, None))
        for attr, expected in expected_attributes.items():
            mismatches.append(Mismatch(attr, expected, None))
        return ComparisonResult(ok=False, mismatches=mismatches)

    if expected_states is not None and actual.state not in expected_states:
        mismatches.append(Mismatch("state", expected_label, actual.state))

    for attr, expected in expected_attributes.items():
        actual_value = actual.attributes.get(attr)
        tolerance = tolerances.get(attr, 0)
        if attr == "rgb_color":
            matched = _values_match(
                _normalized_rgb(expected), _normalized_rgb(actual_value), tolerance
            )
        else:
            matched = _values_match(expected, actual_value, tolerance)
        if not matched:
            mismatches.append(Mismatch(attr, expected, actual_value))

    return ComparisonResult(ok=not mismatches, mismatches=mismatches)
