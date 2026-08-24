"""Generic, tolerance-aware comparison of expected vs. actual state/attributes.

Generalizes the tolerance logic from the original lights/switches watchdog
automation (brightness +/-5, rgb +/-5 per channel, kelvin +/-100, xy +/-0.01)
to any attribute of any domain: scalars are compared with an absolute-value
tolerance, list/tuple values (rgb_color, xy_color, ...) are compared
element-wise with that same tolerance applied to each element, and anything
else (strings, booleans, None) requires an exact match.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import State

from .domain_defaults import SERVICE_DATA_ATTRIBUTE_ALIASES, STATE_SERVICES, TOGGLE_SERVICES
from .models import ComparisonResult, Mismatch


def compute_expected(
    domain: str,
    service: str,
    service_data: dict[str, Any],
    attributes_to_check: list[str],
    current_state: State | None,
) -> tuple[str | None, dict[str, Any]]:
    """Derive the expected state and attributes for a just-issued service call."""
    if service in TOGGLE_SERVICES:
        was_on = current_state is not None and current_state.state == "on"
        expected_state: str | None = "off" if was_on else "on"
    else:
        expected_state = STATE_SERVICES.get(service)

    aliases = SERVICE_DATA_ATTRIBUTE_ALIASES.get((domain, service), {})
    expected_attributes: dict[str, Any] = {}
    for attr in attributes_to_check:
        data_key = aliases.get(attr, attr)
        if data_key in service_data:
            expected_attributes[attr] = service_data[data_key]

    return expected_state, expected_attributes


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
            for exp_item, act_item in zip(expected, actual)
        )
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return False
        return abs(float(expected) - float(actual)) <= tolerance
    return expected == actual


def compare(
    expected_state: str | None,
    expected_attributes: dict[str, Any],
    tolerances: dict[str, float],
    actual: State | None,
) -> ComparisonResult:
    """Compare the current state/attributes of an entity against expectations."""
    mismatches: list[Mismatch] = []

    if actual is None:
        mismatches.append(Mismatch("state", expected_state, None))
        for attr, expected in expected_attributes.items():
            mismatches.append(Mismatch(attr, expected, None))
        return ComparisonResult(ok=False, mismatches=mismatches)

    if expected_state is not None and actual.state != expected_state:
        mismatches.append(Mismatch("state", expected_state, actual.state))

    for attr, expected in expected_attributes.items():
        actual_value = actual.attributes.get(attr)
        tolerance = tolerances.get(attr, 0)
        if not _values_match(expected, actual_value, tolerance):
            mismatches.append(Mismatch(attr, expected, actual_value))

    return ComparisonResult(ok=not mismatches, mismatches=mismatches)
