"""Data models for the Action Control integration."""
from __future__ import annotations

from dataclasses import dataclass, field
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

    name: str
    rule_id: str = field(default_factory=lambda: uuid4().hex)
    enabled: bool = True

    # --- matching ---
    domains: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)  # empty = any service
    entity_id_pattern: str | None = None
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

    # --- movement / change detection ---
    wait_for_change: bool = False
    change_attribute: str | None = None
    change_timeout: float = c.DEFAULT_CHANGE_TIMEOUT

    # --- escalation ---
    escalation_enabled: bool = False
    escalation_action: list[dict[str, Any]] | None = None
    escalation_cooldown: float = c.DEFAULT_ESCALATION_COOLDOWN
    escalation_replay_delay: float = c.DEFAULT_ESCALATION_REPLAY_DELAY

    # --- notification ---
    notify_persistent: bool = True
    notify_service: str | None = None

    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            c.CONF_RULE_ID: self.rule_id,
            c.CONF_NAME: self.name,
            c.CONF_ENABLED: self.enabled,
            c.CONF_DOMAINS: list(self.domains),
            c.CONF_SERVICES: list(self.services),
            c.CONF_ENTITY_ID_PATTERN: self.entity_id_pattern,
            c.CONF_NAME_PATTERN: self.name_pattern,
            c.CONF_AREA_IDS: list(self.area_ids),
            c.CONF_LABEL_IDS: list(self.label_ids),
            c.CONF_DEVICE_IDS: list(self.device_ids),
            c.CONF_CHECK_DELAY: self.check_delay,
            c.CONF_ATTRIBUTES_TO_CHECK: list(self.attributes_to_check),
            c.CONF_TOLERANCES: dict(self.tolerances),
            c.CONF_RETRIES: self.retries,
            c.CONF_RETRY_DELAY: self.retry_delay,
            c.CONF_WAIT_FOR_CHANGE: self.wait_for_change,
            c.CONF_CHANGE_ATTRIBUTE: self.change_attribute,
            c.CONF_CHANGE_TIMEOUT: self.change_timeout,
            c.CONF_ESCALATION_ENABLED: self.escalation_enabled,
            c.CONF_ESCALATION_ACTION: self.escalation_action,
            c.CONF_ESCALATION_COOLDOWN: self.escalation_cooldown,
            c.CONF_ESCALATION_REPLAY_DELAY: self.escalation_replay_delay,
            c.CONF_NOTIFY_PERSISTENT: self.notify_persistent,
            c.CONF_NOTIFY_SERVICE: self.notify_service,
            c.CONF_CREATED_AT: self.created_at,
            c.CONF_UPDATED_AT: self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        return cls(
            rule_id=data.get(c.CONF_RULE_ID) or uuid4().hex,
            name=data.get(c.CONF_NAME, ""),
            enabled=data.get(c.CONF_ENABLED, True),
            domains=list(data.get(c.CONF_DOMAINS, [])),
            services=list(data.get(c.CONF_SERVICES, [])),
            entity_id_pattern=data.get(c.CONF_ENTITY_ID_PATTERN),
            name_pattern=data.get(c.CONF_NAME_PATTERN),
            area_ids=list(data.get(c.CONF_AREA_IDS, [])),
            label_ids=list(data.get(c.CONF_LABEL_IDS, [])),
            device_ids=list(data.get(c.CONF_DEVICE_IDS, [])),
            check_delay=data.get(c.CONF_CHECK_DELAY, c.DEFAULT_CHECK_DELAY),
            attributes_to_check=list(data.get(c.CONF_ATTRIBUTES_TO_CHECK, [])),
            tolerances=dict(data.get(c.CONF_TOLERANCES, {})),
            retries=data.get(c.CONF_RETRIES, c.DEFAULT_RETRIES),
            retry_delay=data.get(c.CONF_RETRY_DELAY, c.DEFAULT_RETRY_DELAY),
            wait_for_change=data.get(c.CONF_WAIT_FOR_CHANGE, False),
            change_attribute=data.get(c.CONF_CHANGE_ATTRIBUTE),
            change_timeout=data.get(c.CONF_CHANGE_TIMEOUT, c.DEFAULT_CHANGE_TIMEOUT),
            escalation_enabled=data.get(c.CONF_ESCALATION_ENABLED, False),
            escalation_action=data.get(c.CONF_ESCALATION_ACTION),
            escalation_cooldown=data.get(
                c.CONF_ESCALATION_COOLDOWN, c.DEFAULT_ESCALATION_COOLDOWN
            ),
            escalation_replay_delay=data.get(
                c.CONF_ESCALATION_REPLAY_DELAY, c.DEFAULT_ESCALATION_REPLAY_DELAY
            ),
            notify_persistent=data.get(c.CONF_NOTIFY_PERSISTENT, True),
            notify_service=data.get(c.CONF_NOTIFY_SERVICE),
            created_at=data.get(c.CONF_CREATED_AT) or _now_iso(),
            updated_at=data.get(c.CONF_UPDATED_AT) or _now_iso(),
        )


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
