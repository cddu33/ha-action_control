"""Data models for the Action Control integration."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from . import const as c


class RuleStatus(StrEnum):
    """Last known outcome of a rule for a given entity."""

    IDLE = c.STATUS_IDLE
    OK = c.STATUS_OK
    RETRYING = c.STATUS_RETRYING
    ESCALATED = c.STATUS_ESCALATED
    FAILED = c.STATUS_FAILED


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class Rule:
    """A single watchdog rule: what to watch, how to verify, how to recover."""

    name: str = ""
    rule_id: str = field(default_factory=lambda: uuid4().hex)
    enabled: bool = True

    # --- matching ---
    domains: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)  # empty = any service
    entity_id_pattern: str | None = None
    # Several are needed in practice: entities exposed twice (a switch_as_x
    # light over its switch) rarely share one prefix.
    entity_id_exclude_patterns: list[str] = field(default_factory=list)
    name_pattern: str | None = None
    area_ids: list[str] = field(default_factory=list)
    label_ids: list[str] = field(default_factory=list)
    device_ids: list[str] = field(default_factory=list)

    # --- verification ---
    check_delay: float = c.DEFAULT_CHECK_DELAY
    attributes_to_check: list[str] = field(default_factory=list)
    tolerances: dict[str, float] = field(default_factory=dict)
    retries: int = c.DEFAULT_RETRIES
    retry_delay: float = c.DEFAULT_RETRY_DELAY
    retry_backoff: str = c.DEFAULT_RETRY_BACKOFF
    log_entity_info: bool = c.DEFAULT_LOG_ENTITY_INFO

    # --- movement / change detection ---
    wait_for_change: bool = False
    change_attribute: str | None = None
    change_timeout: float = c.DEFAULT_CHANGE_TIMEOUT

    # --- escalation ---
    escalation_enabled: bool = False
    escalation_action: list[dict[str, Any]] | None = None
    escalation_cooldown: float = c.DEFAULT_ESCALATION_COOLDOWN
    escalation_replay_delay: float = c.DEFAULT_ESCALATION_REPLAY_DELAY
    # Optional: verify the escalation action actually worked before replaying.
    escalation_check_entity_id: str | None = None
    escalation_check_state: str | None = None
    escalation_check_delay: float = c.DEFAULT_ESCALATION_CHECK_DELAY

    # --- notification ---
    notify_persistent: bool = True
    notify_service: str | None = None

    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Dict keys match field names 1:1 (see const.py)."""
        result: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, list):
                value = list(value)
            elif isinstance(value, dict):
                value = dict(value)
            result[f.name] = value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        """Missing/None keys fall back to the dataclass field defaults."""
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in data or data[f.name] is None:
                continue
            value = data[f.name]
            if isinstance(value, list):
                value = list(value)
            elif isinstance(value, dict):
                value = dict(value)
            elif f.name == c.CONF_RETRIES:
                # The number selector hands back a float, so a retry count
                # reads as "4.0" everywhere it is shown without this.
                value = int(value)
            kwargs[f.name] = value
        return cls(**kwargs)


@dataclass(slots=True)
class Mismatch:
    """A single attribute (or the state) that did not match what was expected."""

    attribute: str
    expected: Any
    actual: Any


@dataclass(slots=True)
class ComparisonResult:
    """Outcome of comparing an entity's actual state against expectations."""

    ok: bool
    mismatches: list[Mismatch] = field(default_factory=list)


@dataclass(slots=True)
class RuleRunStatus:
    """Latest known status of a rule, surfaced on its sensor entity."""

    status: RuleStatus = RuleStatus.IDLE
    entity_id: str | None = None
    expected_state: str | None = None
    expected_attributes: dict[str, Any] = field(default_factory=dict)
    actual_state: str | None = None
    actual_attributes: dict[str, Any] = field(default_factory=dict)
    attempt: int = 0
    mismatches: list[str] = field(default_factory=list)
    last_checked: str | None = None
    response_duration: float | None = None
