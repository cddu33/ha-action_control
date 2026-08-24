"""Unit tests for the generic tolerance-based comparator."""
from __future__ import annotations

from homeassistant.core import State

from custom_components.action_control import comparator


def _state(state: str, **attrs) -> State:
    return State("light.test", state, attrs)


def test_scalar_within_tolerance_ok():
    result = comparator.compare(
        "on", {"brightness": 200}, {"brightness": 5}, _state("on", brightness=203)
    )
    assert result.ok
    assert result.mismatches == []


def test_scalar_outside_tolerance_fails():
    result = comparator.compare(
        "on", {"brightness": 200}, {"brightness": 5}, _state("on", brightness=150)
    )
    assert not result.ok
    assert result.mismatches[0].attribute == "brightness"


def test_list_attribute_elementwise_tolerance():
    result = comparator.compare(
        "on", {"rgb_color": [255, 0, 0]}, {"rgb_color": 5}, _state("on", rgb_color=[252, 3, 1])
    )
    assert result.ok


def test_list_attribute_one_channel_out_of_tolerance():
    result = comparator.compare(
        "on", {"rgb_color": [255, 0, 0]}, {"rgb_color": 5}, _state("on", rgb_color=[255, 50, 0])
    )
    assert not result.ok


def test_state_mismatch_detected():
    result = comparator.compare("off", {}, {}, _state("on"))
    assert not result.ok
    assert result.mismatches[0].attribute == "state"


def test_missing_expected_attribute_ignored():
    result = comparator.compare("on", {}, {}, _state("on", brightness=42))
    assert result.ok


def test_entity_unavailable_fails():
    result = comparator.compare("on", {"brightness": 200}, {}, None)
    assert not result.ok


def test_exact_match_for_non_numeric_attribute():
    result = comparator.compare(None, {"effect": "colorloop"}, {}, _state("on", effect="none"))
    assert not result.ok

    result = comparator.compare(None, {"effect": "colorloop"}, {}, _state("on", effect="colorloop"))
    assert result.ok


def test_compute_expected_toggle_inverts_current_state():
    on_state = _state("on")
    expected_state, _ = comparator.compute_expected("light", "toggle", {}, [], on_state)
    assert expected_state == frozenset({"off"})

    expected_state, _ = comparator.compute_expected("light", "toggle", {}, [], None)
    assert expected_state == frozenset({"on"})


def test_compute_expected_cover_toggle_is_open_closed_not_on_off():
    expected_state, _ = comparator.compute_expected("cover", "toggle", {}, [], _state("open"))
    assert expected_state == frozenset({"closed", "closing"})

    expected_state, _ = comparator.compute_expected("cover", "toggle", {}, [], _state("closed"))
    assert expected_state == frozenset({"open", "opening"})


def test_compute_expected_accepts_transitional_states():
    expected_state, _ = comparator.compute_expected("cover", "open_cover", {}, [], None)
    assert comparator.compare(expected_state, {}, {}, _state("opening")).ok


def test_compute_expected_none_for_a_service_with_no_implied_state():
    for domain, service in (("climate", "turn_on"), ("cover", "stop_cover")):
        expected_state, _ = comparator.compute_expected(domain, service, {}, [], _state("heat"))
        assert expected_state is None


def test_compute_expected_attributes_from_service_data():
    expected_state, expected_attrs = comparator.compute_expected(
        "light",
        "turn_on",
        {"brightness": 128, "rgb_color": [1, 2, 3]},
        ["brightness", "rgb_color", "xy_color"],
        None,
    )
    assert expected_state == frozenset({"on"})
    assert expected_attrs == {"brightness": 128, "rgb_color": [1, 2, 3]}


def test_compute_expected_converts_brightness_pct():
    _, expected_attrs = comparator.compute_expected(
        "light", "turn_on", {"brightness_pct": 50}, ["brightness"], None
    )
    assert expected_attrs == {"brightness": 128}


def test_compute_expected_uses_service_data_alias():
    expected_state, expected_attrs = comparator.compute_expected(
        "cover",
        "set_cover_position",
        {"position": 42},
        ["current_position"],
        None,
    )
    assert expected_state is None
    assert expected_attrs == {"current_position": 42}
