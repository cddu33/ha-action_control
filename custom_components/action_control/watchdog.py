"""Per-(rule, entity) orchestration: early-exit, verify, retry, escalate, replay.

Two modes share the same retry/escalation tail: a delayed snapshot comparison
for anything with a settled state, and a "wait for the attribute to actually
start changing" mode for things that travel, like covers.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.script import Script, async_validate_actions_config

from . import comparator, messages
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
) -> bool:
    """Re-send the command for one entity. A failure must not kill the run."""
    data = _strip_target_keys(service_data)
    ctx = engine.contexts.new_context()
    try:
        await engine.hass.services.async_call(
            domain, service, data, target={"entity_id": entity_id}, context=ctx
        )
    except Exception:  # noqa: BLE001 - the target integration may raise anything
        _LOGGER.exception(
            "Action Control: re-issuing %s.%s on %s failed", domain, service, entity_id
        )
        return False
    return True


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
    hass: HomeAssistant,
    entity_id: str,
    result: ComparisonResult,
    escalated: bool,
) -> str:
    texts = messages.texts_for(hass)
    lines = [messages.render(texts, "failure", entity_id=entity_id)]
    if escalated:
        lines.append(messages.render(texts, "escalated"))
    lines.extend(
        messages.render(
            texts,
            "mismatch_line",
            attribute=mismatch.attribute,
            expected=repr(mismatch.expected),
            actual=repr(mismatch.actual),
        )
        for mismatch in result.mismatches
    )
    return "\n".join(lines)


async def _notify(
    engine: "ActionControlEngine", rule: Rule, entity_id: str, message: str
) -> None:
    hass = engine.hass
    ctx = engine.contexts.new_context()
    title = f"Action Control: {rule.name}"
    if rule.notify_persistent:
        try:
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": title,
                    "message": message,
                    # Stable id: a repeated failure replaces its notification
                    # instead of stacking a new one every single time.
                    "notification_id": f"{DOMAIN}_{rule.rule_id}_{entity_id}",
                },
                context=ctx,
            )
        except Exception:  # noqa: BLE001 - notifying must never break the run
            _LOGGER.exception(
                "Action Control: persistent notification for rule '%s' failed", rule.name
            )
    if rule.notify_service:
        try:
            await hass.services.async_call(
                "notify",
                rule.notify_service,
                {"title": title, "message": message},
                context=ctx,
            )
        except Exception:  # noqa: BLE001 - a missing/broken notify service is the user's
            _LOGGER.exception(
                "Action Control: notify.%s for rule '%s' failed",
                rule.notify_service,
                rule.name,
            )


async def async_run_watchdog(
    engine: "ActionControlEngine",
    rule: Rule,
    entity_id: str,
    domain: str,
    service: str,
    service_data: dict[str, Any],
    expected_state: Any,
    expected_attributes: dict[str, Any],
    run_token: int = 0,
) -> None:
    """Verify a single entity reached the state a service call requested."""
    hass = engine.hass
    lock = engine.lock_for(rule.rule_id, entity_id)

    def _superseded() -> bool:
        """True once a newer command for this entity has been dispatched."""
        return not engine.is_current_run(rule.rule_id, entity_id, run_token)

    async with lock:
        if _superseded():
            _LOGGER.debug(
                "Rule '%s': a newer command for %s superseded this check, dropping it",
                rule.name,
                entity_id,
            )
            return

        status = RuleRunStatus(
            entity_id=entity_id,
            expected_state=comparator.format_expected_state(expected_state),
            expected_attributes=expected_attributes,
        )

        # Early exit: already satisfied right when the event fired (no-op
        # command, or the target integration already applied it instantly).
        result = comparator.compare(
            expected_state, expected_attributes, rule.tolerances, hass.states.get(entity_id)
        )
        if result.ok:
            _LOGGER.debug(
                "Rule '%s': %s already matches the expected state/attributes, nothing to do",
                rule.name,
                entity_id,
            )
            _publish(engine, rule.rule_id, status, RuleStatus.OK, hass.states.get(entity_id))
            return
        _LOGGER.debug(
            "Rule '%s': %s not yet satisfied (mismatches: %s)",
            rule.name,
            entity_id,
            [(m.attribute, m.expected, m.actual) for m in result.mismatches],
        )

        moved = True
        no_movement_mismatch = None
        if rule.wait_for_change and rule.change_attribute:
            baseline = None
            state = hass.states.get(entity_id)
            if state is not None:
                baseline = state.attributes.get(rule.change_attribute)
            attempt = 0
            _LOGGER.debug(
                "Rule '%s': waiting up to %ss for %s.%s on %s to change (baseline=%r)",
                rule.name,
                rule.change_timeout,
                domain,
                service,
                entity_id,
                baseline,
            )
            moved = await _wait_for_attribute_change(
                hass, entity_id, rule.change_attribute, baseline, rule.change_timeout
            )
            while not moved and attempt < rule.retries:
                if _superseded():
                    _LOGGER.debug(
                        "Rule '%s': newer command for %s, abandoning retries",
                        rule.name,
                        entity_id,
                    )
                    return
                attempt += 1
                status.attempt = attempt
                _LOGGER.debug(
                    "Rule '%s': no movement on %s, retry %s/%s (reissuing %s.%s)",
                    rule.name,
                    entity_id,
                    attempt,
                    rule.retries,
                    domain,
                    service,
                )
                _publish(engine, rule.rule_id, status, RuleStatus.RETRYING, hass.states.get(entity_id))
                await _reissue_command(engine, domain, service, entity_id, service_data)
                moved = await _wait_for_attribute_change(
                    hass, entity_id, rule.change_attribute, baseline, rule.change_timeout
                )
            final_state = hass.states.get(entity_id)
            if moved:
                _LOGGER.debug("Rule '%s': %s started moving, verified OK", rule.name, entity_id)
                _publish(engine, rule.rule_id, status, RuleStatus.OK, final_state)
                return
            # No movement detected: this is the failure, independent of
            # whether change_attribute happens to be in attributes_to_check
            # (it usually isn't -- the cover preset relies on this check
            # instead of a snapshot tolerance comparison on position).
            current_value = final_state.attributes.get(rule.change_attribute) if final_state else None
            no_movement_mismatch = Mismatch(
                rule.change_attribute,
                messages.render(
                    messages.texts_for(hass), "different_from", baseline=repr(baseline)
                ),
                current_value,
            )
        else:
            await asyncio.sleep(rule.check_delay)
            attempt = 0
            final_state = hass.states.get(entity_id)
            result = comparator.compare(
                expected_state, expected_attributes, rule.tolerances, final_state
            )
            while not result.ok and attempt < rule.retries:
                if _superseded():
                    _LOGGER.debug(
                        "Rule '%s': newer command for %s, abandoning retries",
                        rule.name,
                        entity_id,
                    )
                    return
                attempt += 1
                status.attempt = attempt
                _LOGGER.debug(
                    "Rule '%s': %s still not satisfied after %ss (mismatches: %s), "
                    "retry %s/%s (reissuing %s.%s)",
                    rule.name,
                    entity_id,
                    rule.check_delay,
                    [(m.attribute, m.expected, m.actual) for m in result.mismatches],
                    attempt,
                    rule.retries,
                    domain,
                    service,
                )
                _publish(engine, rule.rule_id, status, RuleStatus.RETRYING, final_state)
                await _reissue_command(engine, domain, service, entity_id, service_data)
                await asyncio.sleep(rule.retry_delay)
                final_state = hass.states.get(entity_id)
                result = comparator.compare(
                    expected_state, expected_attributes, rule.tolerances, final_state
                )
            if result.ok:
                _LOGGER.debug("Rule '%s': %s verified OK after retry", rule.name, entity_id)
                _publish(engine, rule.rule_id, status, RuleStatus.OK, final_state)
                return

        # Verification failed after all retries (or no movement detected).
        escalated = False
        can_escalate = rule.escalation_enabled and bool(rule.escalation_action)
        if can_escalate and engine.escalation_ready(rule.rule_id):
            escalated = True
            # Armed before running the action: entities failing together must
            # not each fire the recovery action.
            engine.arm_escalation_cooldown(rule.rule_id, rule.escalation_cooldown)
            _LOGGER.debug(
                "Rule '%s': retries exhausted for %s, running escalation action", rule.name, entity_id
            )
            await _run_escalation(engine, hass, rule)
            await asyncio.sleep(rule.escalation_replay_delay)
            _LOGGER.debug(
                "Rule '%s': replaying %s.%s on %s after escalation",
                rule.name,
                domain,
                service,
                entity_id,
            )
            await _reissue_command(engine, domain, service, entity_id, service_data)
        elif can_escalate:
            _LOGGER.debug(
                "Rule '%s': retries exhausted for %s, escalation still in cooldown", rule.name, entity_id
            )
        elif rule.escalation_enabled:
            _LOGGER.warning(
                "Rule '%s': escalation is enabled but no action is configured", rule.name
            )

        final_state = hass.states.get(entity_id)
        result = comparator.compare(
            expected_state, expected_attributes, rule.tolerances, final_state
        )
        if no_movement_mismatch is not None:
            result.mismatches.append(no_movement_mismatch)
            result.ok = False
        texts = messages.texts_for(hass)
        status.actual_state = final_state.state if final_state else None
        status.actual_attributes = dict(final_state.attributes) if final_state else {}
        status.mismatches = [
            messages.render(
                texts,
                "mismatch",
                attribute=m.attribute,
                expected=repr(m.expected),
                actual=repr(m.actual),
            )
            for m in result.mismatches
        ]
        rule_status = RuleStatus.ESCALATED if escalated else RuleStatus.FAILED
        _LOGGER.warning(
            "Rule '%s': %s failed verification (status=%s, mismatches: %s)",
            rule.name,
            entity_id,
            rule_status.value,
            status.mismatches,
        )
        _publish(engine, rule.rule_id, status, rule_status, final_state)

        if rule.notify_persistent or rule.notify_service:
            message = _format_message(hass, entity_id, result, escalated)
            _LOGGER.debug("Rule '%s': sending failure notification for %s", rule.name, entity_id)
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
