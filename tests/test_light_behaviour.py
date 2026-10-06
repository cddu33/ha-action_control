"""Lights end to end: the real Home Assistant light component, simulated bulbs.

The light component converts every request into what the bulb can do --
colors into its own color space, brightness 0 into "off", a color temperature
into a color when there is no white channel -- and the bulb then quantizes and
clips to its gamut. These tests drive that whole chain, so a comparison that
only holds against hand-written states can't pass here.
"""
from __future__ import annotations

import asyncio
import itertools
from typing import Any

import pytest
from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import color as color_util
from pytest_homeassistant_custom_component.common import setup_test_component_platform

from custom_components.action_control.const import DATA_ENGINE, DOMAIN
from custom_components.action_control.domain_defaults import DOMAIN_PRESETS
from custom_components.action_control.models import Rule, RuleStatus
from tests.conftest import make_entry

# The gamut of most Zigbee and recent Hue color bulbs.
GAMUT_C = color_util.GamutType(
    color_util.XYPoint(0.6915, 0.3083),
    color_util.XYPoint(0.17, 0.7),
    color_util.XYPoint(0.1532, 0.0475),
)


class SimLight(LightEntity):
    """A bulb that stores what it is sent the way firmware does.

    xy is clipped to the gamut and quantized to 1/65535, hue and saturation to
    0-254 steps, color temperature to whole mireds within 153-454, brightness
    to 1-254. `deaf` turns it on and off but ignores everything else; `delay`
    and `attribute_delay` make it report late, state first.
    """

    _attr_should_poll = False
    _attr_supported_features = LightEntityFeature.FLASH | LightEntityFeature.TRANSITION

    def __init__(
        self,
        name: str,
        modes: set[ColorMode],
        *,
        gamut: color_util.GamutType | None = None,
        normalizes_rgb: bool = False,
        delay: float = 0,
        attribute_delay: float = 0,
    ) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_supported_color_modes = set(modes)
        self._attr_color_mode = sorted(modes)[0]
        self._attr_min_color_temp_kelvin = 2203
        self._attr_max_color_temp_kelvin = 6535
        self._attr_is_on = False
        self._attr_brightness = None if modes == {ColorMode.ONOFF} else 128
        self._attr_xy_color = (0.3, 0.3)
        self._attr_hs_color = (30, 50)
        self._attr_rgb_color = (255, 128, 0)
        self._attr_rgbw_color = (255, 0, 0, 0)
        self._attr_rgbww_color = (255, 0, 0, 0, 0)
        self._attr_color_temp_kelvin = 3000
        self.gamut = gamut
        self.normalizes_rgb = normalizes_rgb
        self.delay = delay
        self.attribute_delay = attribute_delay
        self.deaf = False
        self.received: list[tuple[str, dict[str, Any]]] = []

    def _report(self, delay: float, apply) -> None:
        def _apply() -> None:
            apply()
            self.async_write_ha_state()

        if delay:
            self.hass.loop.call_later(delay, _apply)
        else:
            _apply()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.received.append(("on", kwargs))
        if "flash" in kwargs:
            return  # blinks, then is back to how it was

        def _on() -> None:
            self._attr_is_on = True

        self._report(self.delay, _on)
        if not self.deaf:
            self._report(self.delay + self.attribute_delay, lambda: self._store(kwargs))

    def _store(self, kwargs: dict[str, Any]) -> None:
        if "brightness" in kwargs:
            self._attr_brightness = min(254, max(1, round(kwargs["brightness"])))
        if "xy_color" in kwargs:
            xy = kwargs["xy_color"]
            if self.gamut and not color_util.check_point_in_lamps_reach(xy, self.gamut):
                xy = color_util.get_closest_point_to_point(xy, self.gamut)
            self._attr_xy_color = (round(xy[0] * 65535) / 65535, round(xy[1] * 65535) / 65535)
            self._attr_color_mode = ColorMode.XY
        if "hs_color" in kwargs:
            hue, sat = kwargs["hs_color"]
            self._attr_hs_color = (round(hue * 254 / 360) * 360 / 254, round(sat * 2.54) / 2.54)
            self._attr_color_mode = ColorMode.HS
        if "rgb_color" in kwargs:
            rgb = tuple(kwargs["rgb_color"])
            if self.normalizes_rgb and max(rgb):
                rgb = tuple(round(v * 255 / max(rgb)) for v in rgb)
            self._attr_rgb_color = rgb
            self._attr_color_mode = ColorMode.RGB
        if "rgbw_color" in kwargs:
            self._attr_rgbw_color = tuple(kwargs["rgbw_color"])
            self._attr_color_mode = ColorMode.RGBW
        if "rgbww_color" in kwargs:
            self._attr_rgbww_color = tuple(kwargs["rgbww_color"])
            self._attr_color_mode = ColorMode.RGBWW
        if "color_temp_kelvin" in kwargs:
            mired = min(454, max(153, round(1e6 / kwargs["color_temp_kelvin"])))
            self._attr_color_temp_kelvin = round(1e6 / mired)
            self._attr_color_mode = ColorMode.COLOR_TEMP

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.received.append(("off", kwargs))

        def _off() -> None:
            self._attr_is_on = False

        self._report(self.delay, _off)


def _bulbs() -> list[SimLight]:
    return [
        SimLight("xy_ct", {ColorMode.XY, ColorMode.COLOR_TEMP}, gamut=GAMUT_C),
        SimLight("xy_only", {ColorMode.XY}, gamut=GAMUT_C),
        SimLight("hs_ct", {ColorMode.HS, ColorMode.COLOR_TEMP}),
        SimLight("rgb_raw", {ColorMode.RGB}),
        SimLight("rgb_normalizing", {ColorMode.RGB}, normalizes_rgb=True),
        SimLight("rgbw_ct", {ColorMode.RGBW, ColorMode.COLOR_TEMP}),
        SimLight("rgbww", {ColorMode.RGBWW}),
        SimLight("ct_only", {ColorMode.COLOR_TEMP}),
        SimLight("dimmer", {ColorMode.BRIGHTNESS}),
        SimLight("onoff", {ColorMode.ONOFF}),
    ]


COMMANDS: list[tuple[str, dict[str, Any]]] = [
    ("turn_on", {}),
    ("turn_on", {"brightness": 1}),
    ("turn_on", {"brightness": 255}),
    ("turn_on", {"brightness_pct": 1}),
    ("turn_on", {"brightness_pct": 37}),
    ("turn_on", {"brightness": 0}),
    ("turn_on", {"brightness_pct": 0}),
    ("turn_on", {"rgb_color": [255, 0, 0]}),
    ("turn_on", {"rgb_color": [0, 255, 0]}),
    ("turn_on", {"rgb_color": [0, 0, 255]}),
    ("turn_on", {"rgb_color": [255, 255, 255]}),
    ("turn_on", {"rgb_color": [255, 128, 0]}),
    ("turn_on", {"rgb_color": [255, 0, 255]}),
    ("turn_on", {"rgb_color": [200, 0, 0]}),
    ("turn_on", {"rgb_color": [100, 50, 25]}),
    ("turn_on", {"rgb_color": [20, 10, 4]}),
    ("turn_on", {"rgb_color": [255, 180, 107], "brightness": 150}),
    ("turn_on", {"color_temp_kelvin": 1800}),
    ("turn_on", {"color_temp_kelvin": 2700}),
    ("turn_on", {"color_temp_kelvin": 4000}),
    ("turn_on", {"color_temp_kelvin": 9000}),
    ("turn_on", {"xy_color": [0.3, 0.3]}),
    ("turn_on", {"xy_color": [0.7, 0.29]}),
    ("turn_on", {"xy_color": [0.15, 0.06]}),
    ("turn_on", {"hs_color": [240, 100]}),
    ("turn_on", {"hs_color": [30, 40], "brightness_pct": 60}),
    ("turn_on", {"color_name": "orange"}),
    ("turn_on", {"rgbw_color": [255, 128, 0, 10]}),
    ("turn_on", {"rgbww_color": [255, 128, 0, 10, 200]}),
    ("turn_on", {"flash": "short"}),
    ("turn_on", {"effect": "rainbow"}),
    ("turn_on", {"brightness_step_pct": 10}),
    ("turn_on", {"brightness_step_pct": -10}),
    ("turn_on", {"brightness_step_pct": -100}),
    ("toggle", {}),
    ("toggle", {"brightness": 200}),
    ("turn_off", {}),
    ("turn_off", {"transition": 0}),
]


def _light_rule(**overrides: Any) -> Rule:
    preset = DOMAIN_PRESETS["light"]
    fields: dict[str, Any] = dict(
        name="Lights",
        domains=["light"],
        services=["turn_on", "turn_off", "toggle"],
        attributes_to_check=list(preset["attributes_to_check"]),
        tolerances=dict(preset["tolerances"]),
        retries=0,
        # Not 0: the check must not depend on the bulb answering within the
        # same loop iteration as the call.
        check_delay=0.005,
    )
    fields.update(overrides)
    return Rule(**fields)


async def _setup(
    hass: HomeAssistant,
    bulbs: list[SimLight],
    rule: Rule,
    *,
    extra_light_platforms: list[dict[str, Any]] | None = None,
):
    setup_test_component_platform(hass, "light", bulbs)
    platforms: list[dict[str, Any]] = [{"platform": "test"}]
    if extra_light_platforms:
        platforms.extend(extra_light_platforms)
    assert await async_setup_component(hass, "light", {"light": platforms})
    entry = make_entry(rule)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    notifications: list[str] = []
    hass.services.async_register(
        "persistent_notification",
        "create",
        lambda call: notifications.append(call.data["message"]),
    )
    return hass.data[DOMAIN][entry.entry_id][DATA_ENGINE], notifications


async def _command(hass: HomeAssistant, entity_id: str, service: str, data: dict) -> None:
    await hass.services.async_call("light", service, {"entity_id": entity_id, **data})
    await hass.async_block_till_done()


async def test_a_bulb_that_obeys_is_never_reported(hass):
    """Every bulb type, every kind of request, from off and from on."""
    bulbs = _bulbs()
    rule = _light_rule()
    engine, _ = await _setup(hass, bulbs, rule)

    false_failures = []
    for bulb, start, (service, data) in itertools.product(bulbs, ("off", "on"), COMMANDS):
        await _command(hass, bulb.entity_id, f"turn_{start}", {})
        engine.rule_status.clear()
        await _command(hass, bulb.entity_id, service, data)
        status = engine.rule_status.get(rule.rule_id)
        if status is not None and status.status is not RuleStatus.OK:
            false_failures.append((bulb.entity_id, start, service, data, status.mismatches))

    assert false_failures == []


_GROUP_COMMANDS: list[tuple[str, dict[str, Any]]] = [
    ("turn_on", {}),
    ("turn_on", {"brightness": 150}),
    ("turn_on", {"rgb_color": [0, 0, 255]}),
    ("turn_on", {"color_temp_kelvin": 4000}),
    ("turn_off", {}),
]


async def test_a_mixed_light_group_is_never_falsely_reported(hass):
    """A group of unlike bulbs (RGB, color-temp-only, on/off) must not
    produce a false failure -- the group aggregates its members' attributes
    (mean of the "on" ones) and picks its own color mode by majority, which
    does not always land where a single bulb's math would predict."""
    bulbs = _bulbs()
    rule = _light_rule(entity_id_pattern="light.mixed_group")
    engine, _ = await _setup(
        hass,
        bulbs,
        rule,
        extra_light_platforms=[
            {
                "platform": "group",
                "name": "Mixed Group",
                "entities": ["light.rgb_raw", "light.ct_only", "light.onoff"],
            }
        ],
    )

    false_failures = []
    for start, (service, data) in itertools.product(("off", "on"), _GROUP_COMMANDS):
        await _command(hass, "light.mixed_group", f"turn_{start}", {})
        engine.rule_status.clear()
        await _command(hass, "light.mixed_group", service, data)
        status = engine.rule_status.get(rule.rule_id)
        if status is not None and status.status is not RuleStatus.OK:
            false_failures.append((start, service, data, status.mismatches))

    assert false_failures == []


@pytest.mark.parametrize(
    ("bulb_index", "data"),
    [
        (0, {"rgb_color": [0, 0, 255]}),  # xy + ct
        (1, {"hs_color": [120, 100]}),  # xy only
        (1, {"color_temp_kelvin": 2700}),  # xy only: emulated in color
        (2, {"rgb_color": [0, 0, 255]}),  # hs + ct
        (3, {"rgb_color": [0, 0, 255]}),  # rgb
        (5, {"rgb_color": [0, 0, 255]}),  # rgbw
        (6, {"color_name": "blue"}),  # rgbww
        (0, {"color_temp_kelvin": 6500}),
        (7, {"color_temp_kelvin": 6500}),
        (8, {"brightness": 30}),
    ],
)
async def test_a_bulb_that_ignores_the_request_is_reported(hass, bulb_index, data):
    bulbs = _bulbs()
    rule = _light_rule()
    engine, notifications = await _setup(hass, bulbs, rule)
    bulb = bulbs[bulb_index]
    await _command(hass, bulb.entity_id, "turn_on", {})
    bulb.deaf = True

    await _command(hass, bulb.entity_id, "turn_on", data)

    assert engine.rule_status[rule.rule_id].status is RuleStatus.FAILED
    assert len(notifications) == 1


async def test_a_toggle_is_not_replayed_as_a_toggle(hass):
    """The light came on but its brightness is late: replaying the toggle
    would turn it off again."""
    bulb = SimLight("slow", {ColorMode.BRIGHTNESS}, attribute_delay=0.2)
    rule = _light_rule(retries=1, check_delay=0.05, retry_delay=0.3)
    await _setup(hass, [bulb], rule)

    await _command(hass, bulb.entity_id, "toggle", {"brightness": 200})
    await asyncio.sleep(0.5)
    await hass.async_block_till_done()

    assert [service for service, _ in bulb.received] == ["on", "on"]
    state = hass.states.get(bulb.entity_id)
    assert (state.state, state.attributes["brightness"]) == ("on", 200)


async def test_dimming_down_an_off_light_is_not_a_failure(hass):
    """A dimmer's "down" pressed while the light is off: Home Assistant steps
    from zero and leaves it off, which is all the press could do."""
    bulb = SimLight("dimmed", {ColorMode.BRIGHTNESS})
    rule = _light_rule(retries=2, check_delay=0.05, retry_delay=0.05)
    _, notifications = await _setup(hass, [bulb], rule)

    await _command(hass, bulb.entity_id, "turn_on", {"brightness_step_pct": -10})
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()

    assert hass.states.get(bulb.entity_id).state == "off"
    assert notifications == []
    assert len(bulb.received) == 1  # nothing replayed


async def test_a_flash_is_neither_reported_nor_repeated(hass):
    bulb = SimLight("flashing", {ColorMode.BRIGHTNESS})
    rule = _light_rule(retries=2, check_delay=0.05, retry_delay=0.05)
    _, notifications = await _setup(hass, [bulb], rule)

    await _command(hass, bulb.entity_id, "turn_on", {"flash": "short"})
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()

    assert notifications == []
    assert len(bulb.received) == 1


async def test_a_check_cancelled_by_an_unwatched_service_leaves_no_retrying(hass):
    """The sensor must not stay on "retrying" when the command that cancelled
    the check is one the rule doesn't watch."""
    bulb = SimLight("slow", {ColorMode.BRIGHTNESS}, delay=0.15)
    rule = _light_rule(services=["turn_on"], retries=2, check_delay=0.05, retry_delay=1)
    engine, _ = await _setup(hass, [bulb], rule)

    await hass.services.async_call("light", "turn_on", {"entity_id": bulb.entity_id})
    await asyncio.sleep(0.1)
    assert engine.rule_status[rule.rule_id].status is RuleStatus.RETRYING

    await _command(hass, bulb.entity_id, "turn_off", {})

    assert engine.rule_status[rule.rule_id].status is RuleStatus.IDLE
    # Let the slow bulb report: a timer left pending fails the test teardown.
    await asyncio.sleep(0.2)
    await hass.async_block_till_done()
