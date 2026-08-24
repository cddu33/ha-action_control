"""Shared fixtures for Action Control tests."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.action_control.const import DOMAIN, OPT_GLOBAL, OPT_RULES
from custom_components.action_control.models import Rule


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/ discoverable during tests."""
    yield


def make_light_rule(**overrides) -> Rule:
    defaults = dict(
        name="Lights watchdog",
        domains=["light"],
        services=["turn_on", "turn_off", "toggle"],
        attributes_to_check=["brightness", "rgb_color"],
        tolerances={"brightness": 5, "rgb_color": 5},
        retries=2,
        retry_delay=0,
        check_delay=0,
    )
    defaults.update(overrides)
    return Rule(**defaults)


def make_cover_rule(**overrides) -> Rule:
    defaults = dict(
        name="Cover watchdog",
        domains=["cover"],
        services=["open_cover", "close_cover", "set_cover_position"],
        entity_id_pattern="cover.volet_*",
        wait_for_change=True,
        change_attribute="current_position",
        change_timeout=0.1,
        retries=1,
        escalation_enabled=True,
        escalation_action=[
            {"action": "script.restart_velux", "data": {}}
        ],
        escalation_cooldown=300,
        escalation_replay_delay=0,
    )
    defaults.update(overrides)
    return Rule(**defaults)


def make_entry(rule: Rule, *, enabled: bool = True) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Action Control",
        options={
            OPT_RULES: {rule.rule_id: rule.to_dict()},
            OPT_GLOBAL: {"enabled": enabled},
        },
    )


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    return make_entry(make_light_rule())


@pytest.fixture
def mock_cover_config_entry() -> MockConfigEntry:
    return make_entry(make_cover_rule())
