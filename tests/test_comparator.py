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


# ---- what Home Assistant really makes of a light turn-on ----


def _light(state: str = "on", **attrs) -> State:
    return State("light.test", state, attrs)


def test_compute_expected_brightness_zero_means_off():
    for data in ({"brightness": 0}, {"brightness_pct": 0}):
        expected_state, expected_attrs = comparator.compute_expected(
            "light", "turn_on", data, ["brightness"], _light()
        )
        assert expected_state == frozenset({"off"})
        assert expected_attrs == {}


def test_compute_expected_toggle_to_off_expects_no_attributes():
    expected_state, expected_attrs = comparator.compute_expected(
        "light", "toggle", {"brightness": 200}, ["brightness"], _light("on")
    )
    assert expected_state == frozenset({"off"})
    assert expected_attrs == {}


def test_compute_expected_clamps_color_temp_to_the_light_range():
    light = _light(
        supported_color_modes=["color_temp"],
        min_color_temp_kelvin=2700,
        max_color_temp_kelvin=6500,
    )
    _, expected_attrs = comparator.compute_expected(
        "light", "turn_on", {"color_temp_kelvin": 2000}, ["color_temp_kelvin"], light
    )
    assert expected_attrs == {"color_temp_kelvin": 2700}


def test_compute_expected_checks_an_emulated_color_temp_as_a_color():
    # A color-only light emulates a color temperature in hs: it then reports
    # no color_temp_kelvin at all, but the color it shows instead.
    _, expected_attrs = comparator.compute_expected(
        "light",
        "turn_on",
        {"color_temp_kelvin": 3000},
        ["color_temp_kelvin"],
        _light(supported_color_modes=["hs"]),
    )
    assert set(expected_attrs) == {"xy_color"}
    assert comparator.compare(
        "on", expected_attrs, {}, _light(xy_color=(0.49, 0.38))
    ).ok


def test_compute_expected_drops_what_the_light_cannot_report():

    _, expected_attrs = comparator.compute_expected(
        "light",
        "turn_on",
        {"brightness": 100, "rgb_color": [255, 0, 0]},
        ["brightness", "rgb_color"],
        _light(supported_color_modes=["onoff"]),
    )
    assert expected_attrs == {}


def test_rgb_color_is_compared_as_a_hue_not_as_raw_values():
    # An hs or xy light reports its color at full intensity.
    assert comparator.compare(
        "on", {"rgb_color": [200, 0, 0]}, {"rgb_color": 5}, _light(rgb_color=(255, 0, 0))
    ).ok
    assert not comparator.compare(
        "on", {"rgb_color": [200, 0, 0]}, {"rgb_color": 5}, _light(rgb_color=(0, 0, 255))
    ).ok


def test_compute_expected_maps_legacy_mireds_color_temp():
    # color_temp (mireds) was accepted by light.turn_on until HA 2026.1;
    # installs on an older core still send it.
    _, expected_attrs = comparator.compute_expected(
        "light", "turn_on", {"color_temp": 370}, ["color_temp_kelvin"], None
    )
    assert expected_attrs == {"color_temp_kelvin": round(1e6 / 370)}


def test_compute_expected_mireds_loses_to_color_temp_kelvin_when_both_are_sent():
    _, expected_attrs = comparator.compute_expected(
        "light",
        "turn_on",
        {"color_temp_kelvin": 2700, "color_temp": 370},
        ["color_temp_kelvin"],
        None,
    )
    assert expected_attrs == {"color_temp_kelvin": 2700}


def test_a_light_group_s_color_is_never_verified():
    # A group's color is a mean across whichever "on" members happen to
    # report it, with the color mode itself picked by majority vote: neither
    # is predictable from a single member's math.
    group = _light(
        supported_color_modes=["rgb", "color_temp"],
        entity_id=["light.a", "light.b"],
    )
    _, expected_attrs = comparator.compute_expected(
        "light",
        "turn_on",
        {"rgb_color": [0, 0, 255], "color_temp_kelvin": 4000, "brightness": 150},
        ["rgb_color", "xy_color", "color_temp_kelvin", "brightness"],
        group,
    )
    assert expected_attrs == {"brightness": 150}


# ---- climate/humidifier: Home Assistant rejects an out-of-range request ----


def _climate(state: str = "heat", **attrs) -> State:
    return State("climate.test", state, attrs)


def test_compute_expected_excludes_an_out_of_range_set_temperature():
    climate = _climate(min_temp=7, max_temp=35)
    for temperature in (5, 40):
        expected_state, expected_attrs = comparator.compute_expected(
            "climate", "set_temperature", {"temperature": temperature}, ["temperature"], climate
        )
        assert expected_state is None
        assert expected_attrs == {}


def test_compute_expected_keeps_an_in_range_set_temperature():
    climate = _climate(min_temp=7, max_temp=35)
    expected_state, expected_attrs = comparator.compute_expected(
        "climate", "set_temperature", {"temperature": 21}, ["temperature"], climate
    )
    assert expected_state is None
    assert expected_attrs == {"temperature": 21}


def test_compute_expected_excludes_out_of_range_humidifier_humidity():
    humidifier = State(
        "humidifier.test", "on", {"min_humidity": 30, "max_humidity": 80}
    )
    expected_state, expected_attrs = comparator.compute_expected(
        "humidifier", "set_humidity", {"humidity": 95}, ["humidity"], humidifier
    )
    assert expected_state is None
    assert expected_attrs == {}


def test_compute_expected_range_exclusion_ignored_without_min_max_attributes():
    climate = _climate()
    expected_state, expected_attrs = comparator.compute_expected(
        "climate", "set_temperature", {"temperature": 500}, ["temperature"], climate
    )
    assert expected_attrs == {"temperature": 500}


# ---- siren: a duration auto-reverts, like a light's flash ----


def _siren(state: str = "off", **attrs) -> State:
    return State("siren.test", state, attrs)


def test_compute_expected_siren_duration_is_never_verified():
    expected_state, expected_attrs = comparator.compute_expected(
        "siren", "turn_on", {"duration": 5}, [], _siren()
    )
    assert expected_state is None
    assert expected_attrs == {}


def test_compute_expected_siren_without_duration_expects_on():
    expected_state, expected_attrs = comparator.compute_expected(
        "siren", "turn_on", {"tone": "alarm"}, [], _siren()
    )
    assert expected_state == frozenset({"on"})


def test_compute_expected_siren_toggle_with_duration_is_never_verified():
    expected_state, expected_attrs = comparator.compute_expected(
        "siren", "toggle", {"duration": 5}, [], _siren("off")
    )
    assert expected_state is None
    assert expected_attrs == {}


def test_compute_expected_light_turn_on_is_unaffected_by_the_siren_adjuster():
    expected_state, expected_attrs = comparator.compute_expected(
        "light", "turn_on", {"brightness": 100}, ["brightness"], None
    )
    assert expected_state == frozenset({"on"})
    assert expected_attrs == {"brightness": 100}


def test_compute_expected_light_effect_is_never_verified():
    expected_state, expected_attrs = comparator.compute_expected(
        "light", "turn_on", {"effect": "rainbow"}, ["brightness", "rgb_color"], _light()
    )
    assert expected_state is None
    assert expected_attrs == {}


# ---- vacuum/media_player: state modeling for the previously-silent domains ----


def test_compute_expected_vacuum_services():
    cases = {
        "start": "cleaning",
        "pause": "paused",
        "stop": "idle",
        "clean_spot": "cleaning",
    }
    for service, state in cases.items():
        expected_state, _ = comparator.compute_expected("vacuum", service, {}, [], None)
        assert expected_state == frozenset({state})

    expected_state, _ = comparator.compute_expected("vacuum", "return_to_base", {}, [], None)
    assert expected_state == frozenset({"returning", "docked"})


def test_compute_expected_media_player_services():
    expected_state, _ = comparator.compute_expected("media_player", "turn_off", {}, [], None)
    assert expected_state == frozenset({"off"})

    expected_state, _ = comparator.compute_expected("media_player", "turn_on", {}, [], None)
    assert expected_state == frozenset({"on", "idle", "playing", "paused", "buffering"})

    expected_state, _ = comparator.compute_expected("media_player", "media_pause", {}, [], None)
    assert expected_state == frozenset({"paused"})
