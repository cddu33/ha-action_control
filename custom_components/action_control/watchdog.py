"""Per-(rule, entity) orchestration: early-exit, verify, retry, escalate, replay.

This is the direct generalization of both original automations:
- the delayed check + tolerant retry loop generalizes the lights/switches
  watchdog to any domain/attribute;
- the "wait for an attribute to actually start changing" mode generalizes
  the covers watchdog's wait_template/timeout/continue_on_timeout movement
  detection;
- escalation + cooldown + replay generalizes the KLF200 gateway-restart
  pattern into a per-rule configurable recovery action, with the cooldown
  replacing the external guard-switch entirely.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.script import Script, async_validate_actions_config

from . import comparator
from .const import DOMAIN
from .models import ComparisonResult, Mismatch, Rule, RuleRunStatus, RuleStatus

if TYPE_CHECKING:
    from .coordinator import ActionControlEngine

_LOGGER = logging.getLogger(__name__)

_TARGET_KEYS = {"entity_id", "device_id", "area_id", "label_id", "floor_id"}


def _strip_target_keys(service_data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in service_data.items() if k not in _TARGET_KEYS}


async def _wait_for_attribute_change(
    hass: HomeAssistant, entity_id: str, attribute: str, baseline: Any, timeout: float
) -> bool:
    """Wait until `attribute` differs from `baseline`. Return False on timeout."""
    changed = asyncio.Event()

    def _on_change(event: Event) -> None:
        new_state: State | None = event.data.get("new_state")
        if new_state is not None and new_state.attributes.get(attribute) != baseline:
            changed.set()

    unsub = async_track_state_change_event(hass, [entity_id], _on_change)
    try:
        await asyncio.wait_for(changed.wait(), timeout=timeout)
        return True
    except TimeoutError:
        return False
    finally:
        unsub()


async def _reissue_command(
    engine: "ActionControlEngine",
    domain: str,
    service: str,
    entity_id: str,
    service_data: dict[str, Any],
) -> None:
    data = _strip_target_keys(service_data)
    ctx = engine.contexts.new_context()
    await engine.hass.services.async_call(
        domain, service, data, target={"entity_id": entity_id}, context=ctx
    )


async def _run_escalation(
    engine: "ActionControlEngine", hass: HomeAssistant, rule: Rule
) -> None:
    if not rule.escalation_action:
        return
    try:
        validated = await async_validate_actions_config(hass, rule.escalation_action)
        script = Script(hass, validated, f"{DOMAIN}_{rule.rule_id}_escalation", DOMAIN)
        ctx = engine.contexts.new_context()
        await script.async_run(context=ctx)
    except Exception:  # noqa: BLE001 - a bad user-configured action must not crash the watchdog
        _LOGGER.exception(
            "Action Control: escalation action for rule '%s' failed", rule.name
        )


def _format_message(
    entity_id: str,
    expected_state: str | None,
    result: ComparisonResult,
    escalated: bool,
) -> str:
    lines = [f"{entity_id} n'a pas atteint l'état/les attributs demandés."]
    if escalated:
        lines.append("Une action de secours a été déclenchée et la commande rejouée.")
    for mismatch in result.mismatches:
        lines.append(
            f"- {mismatch.attribute}: attendu {mismatch.expected!r}, "
            f"actuel {mismatch.actual!r}"
        )
    return "\n".join(lines)


async def _notify(
    engine: "ActionControlEngine", rule: Rule, entity_id: str, message: str
) -> None:
    hass = engine.hass
    ctx = engine.contexts.new_context()
    if rule.notify_persistent:
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {"title": f"Action Control: {rule.name}", "message": message},
            context=ctx,
        )
    if rule.notify_service:
        await hass.services.async_call(
            "notify",
            rule.notify_service,
            {"title": f"Action Control: {rule.name}", "message": message},
            context=ctx,
        )


async def async_run_watchdog(
    engine: "ActionControlEngine",
    rule: Rule,
    entity_id: str,
    domain: str,
    service: str,
    service_data: dict[str, Any],
    expected_state: str | None,
    expected_attributes: dict[str, Any],
) -> None:
    """Verify a single entity reached the state a service call requested."""
    hass = engine.hass
    lock = engine.lock_for(rule.rule_id, entity_id)
    async with lock:
        status = RuleRunStatus(
            entity_id=entity_id,
            expected_state=expected_state,
            expected_attributes=expected_attributes,
        )

        # Early exit: already satisfied right when the event fired (no-op
        # command, or the target integration already applied it instantly).
        result = comparator.compare(
            expected_state, expected_attributes, rule.tolerances, hass.states.get(entity_id)
        )
        if result.ok:
            _publish(engine, rule.rule_id, status, RuleStatus.OK, hass.states.get(entity_id))
            return

        moved = True
        no_movement_mismatch = None
        if rule.wait_for_change and rule.change_attribute:
            baseline = None
            state = hass.states.get(entity_id)
            if state is not None:
                baseline = state.attributes.get(rule.change_attribute)
            attempt = 0
            moved = await _wait_for_attribute_change(
                hass, entity_id, rule.change_attribute, baseline, rule.change_timeout
            )
            while not moved and attempt < rule.retries:
                attempt += 1
                status.attempt = attempt
                _publish(engine, rule.rule_id, status, RuleStatus.RETRYING, hass.states.get(entity_id))
                await _reissue_command(engine, domain, service, entity_id, service_data)
                moved = await _wait_for_attribute_change(
                    hass, entity_id, rule.change_attribute, baseline, rule.change_timeout
                )
            final_state = hass.states.get(entity_id)
            if moved:
                _publish(engine, rule.rule_id, status, RuleStatus.OK, final_state)
                return
            # No movement detected: this is the failure, independent of
            # whether change_attribute happens to be in attributes_to_check
            # (it usually isn't -- the cover preset relies on this check
            # instead of a snapshot tolerance comparison on position).
            current_value = final_state.attributes.get(rule.change_attribute) if final_state else None
            no_movement_mismatch = Mismatch(
                rule.change_attribute, f"différent de {baseline!r}", current_value
            )
        else:
            await asyncio.sleep(rule.check_delay)
            attempt = 0
            final_state = hass.states.get(entity_id)
            result = comparator.compare(
                expected_state, expected_attributes, rule.tolerances, final_state
            )
            while not result.ok and attempt < rule.retries:
                attempt += 1
                status.attempt = attempt
                _publish(engine, rule.rule_id, status, RuleStatus.RETRYING, final_state)
                await _reissue_command(engine, domain, service, entity_id, service_data)
                await asyncio.sleep(rule.retry_delay)
                final_state = hass.states.get(entity_id)
                result = comparator.compare(
                    expected_state, expected_attributes, rule.tolerances, final_state
                )
            if result.ok:
                _publish(engine, rule.rule_id, status, RuleStatus.OK, final_state)
                return

        # Verification failed after all retries (or no movement detected).
        escalated = False
        if rule.escalation_enabled and engine.escalation_ready(rule.rule_id):
            escalated = True
            await _run_escalation(engine, hass, rule)
            engine.arm_escalation_cooldown(rule.rule_id, rule.escalation_cooldown)
            await asyncio.sleep(rule.escalation_replay_delay)
            await _reissue_command(engine, domain, service, entity_id, service_data)

        final_state = hass.states.get(entity_id)
        result = comparator.compare(
            expected_state, expected_attributes, rule.tolerances, final_state
        )
        if no_movement_mismatch is not None:
            result.mismatches.append(no_movement_mismatch)
            result.ok = False
        status.actual_state = final_state.state if final_state else None
        status.actual_attributes = dict(final_state.attributes) if final_state else {}
        status.mismatches = [
            f"{m.attribute}: attendu {m.expected!r}, actuel {m.actual!r}"
            for m in result.mismatches
        ]
        rule_status = RuleStatus.ESCALATED if escalated else RuleStatus.FAILED
        _publish(engine, rule.rule_id, status, rule_status, final_state)

        if rule.notify_persistent or rule.notify_service:
            message = _format_message(entity_id, expected_state, result, escalated)
            await _notify(engine, rule, entity_id, message)


def _publish(
    engine: "ActionControlEngine",
    rule_id: str,
    status: RuleRunStatus,
    outcome: RuleStatus,
    state: State | None,
) -> None:
    status.status = outcome
    status.actual_state = state.state if state else None
    status.actual_attributes = dict(state.attributes) if state else {}
    status.last_checked = datetime.now(timezone.utc).isoformat()
    engine.set_status(rule_id, status)
