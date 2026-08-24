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
        if not _values_match(expected, actual_value, tolerance):
            mismatches.append(Mismatch(attr, expected, actual_value))

    return ComparisonResult(ok=not mismatches, mismatches=mismatches)
