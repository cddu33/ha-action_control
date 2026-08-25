"""Per-(rule, entity) orchestration: early-exit, verify, retry, escalate, replay.

Two modes share the same retry/escalation tail: a delayed snapshot comparison
for anything with a settled state, and a "wait for the attribute to actually
start changing" mode for things that travel, like covers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.script import Script, async_validate_actions_config

from . import comparator, messages
from .const import DOMAIN, MAX_RETRY_DELAY, RETRY_BACKOFF_EXPONENTIAL, RETRY_BACKOFF_LINEAR
from .models import ComparisonResult, Mismatch, Rule, RuleRunStatus, RuleStatus

if TYPE_CHECKING:
    from .coordinator import ActionControlEngine

_LOGGER = logging.getLogger(__name__)

_TARGET_KEYS = {"entity_id", "device_id", "area_id", "label_id", "floor_id"}


def _strip_target_keys(service_data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in service_data.items() if k not in _TARGET_KEYS}


def _compute_retry_delay(base_delay: float, backoff: str, attempt: int) -> float:
    """Delay before the next retry, given the backoff mode and attempt number.

    `attempt` is 1 for the first retry. `constant` (the default) reproduces
    the original fixed-delay behavior exactly. Growth is capped so a high
    retry count combined with exponential backoff can't produce an
    absurdly long wait.
    """
    if backoff == RETRY_BACKOFF_LINEAR:
        delay = base_delay * attempt
    elif backoff == RETRY_BACKOFF_EXPONENTIAL:
        delay = base_delay * (2 ** (attempt - 1))
    else:
        delay = base_delay
    return min(delay, MAX_RETRY_DELAY)


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


async def _safe_call(
    action: Callable[[], Awaitable[None]], log_msg: str, *log_args: Any
) -> bool:
    """Run a fire-and-forget action; a failure must not kill the watchdog run."""
    try:
        await action()
    except Exception:  # noqa: BLE001 - downstream integrations/user actions may raise anything
        _LOGGER.exception(log_msg, *log_args)
        return False
    return True


async def _reissue_command(
    engine: ActionControlEngine,
    domain: str,
    service: str,
    entity_id: str,
    service_data: dict[str, Any],
) -> bool:
    data = _strip_target_keys(service_data)
    ctx = engine.contexts.new_context()
    return await _safe_call(
        lambda: engine.hass.services.async_call(
            domain, service, data, target={"entity_id": entity_id}, context=ctx
        ),
        "Action Control: re-issuing %s.%s on %s failed",
        domain,
        service,
        entity_id,
    )


async def _run_escalation(
    engine: ActionControlEngine, hass: HomeAssistant, rule: Rule
) -> bool:
    if not rule.escalation_action:
        return False

    async def _run() -> None:
        validated = await async_validate_actions_config(hass, rule.escalation_action)
        script = Script(hass, validated, f"{DOMAIN}_{rule.rule_id}_escalation", DOMAIN)
        await script.async_run(context=engine.contexts.new_context())

    return await _safe_call(
        _run, "Action Control: escalation action for rule '%s' failed", rule.name
    )


def _escalation_check_ok(hass: HomeAssistant, entity_id: str, expected_state: str) -> bool:
    state = hass.states.get(entity_id)
    return state is not None and state.state == expected_state


async def _verify_escalation(
    engine: ActionControlEngine, hass: HomeAssistant, rule: Rule
) -> None:
    """Confirm the escalation action worked, re-running it if it didn't.

    Never blocks the replay that follows: a failure here is logged, not
    raised, so the original command still gets replayed as a last resort.
    """
    await asyncio.sleep(rule.escalation_check_delay)
    ok = _escalation_check_ok(hass, rule.escalation_check_entity_id, rule.escalation_check_state)
    attempt = 0
    while not ok and attempt < rule.retries:
        attempt += 1
        _LOGGER.debug(
            "Rule '%s': escalation target %s not yet '%s', retry %s/%s",
            rule.name,
            rule.escalation_check_entity_id,
            rule.escalation_check_state,
            attempt,
            rule.retries,
        )
        await _run_escalation(engine, hass, rule)
        await asyncio.sleep(_compute_retry_delay(rule.retry_delay, rule.retry_backoff, attempt))
        ok = _escalation_check_ok(
            hass, rule.escalation_check_entity_id, rule.escalation_check_state
        )
    if ok:
        _LOGGER.debug("Rule '%s': escalation action verified OK", rule.name)
    else:
        _LOGGER.warning(
            "Rule '%s': escalation target %s never reached '%s' after %s attempt(s)",
            rule.name,
            rule.escalation_check_entity_id,
            rule.escalation_check_state,
            attempt,
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
    engine: ActionControlEngine, rule: Rule, entity_id: str, message: str
) -> None:
    hass = engine.hass
    ctx = engine.contexts.new_context()
    title = f"Action Control: {rule.name}"
    if rule.notify_persistent:
        await _safe_call(
            lambda: hass.services.async_call(
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
            ),
            "Action Control: persistent notification for rule '%s' failed",
            rule.name,
        )
    if rule.notify_service:
        await _safe_call(
            lambda: hass.services.async_call(
                "notify",
                rule.notify_service,
                {"title": title, "message": message},
                context=ctx,
            ),
            "Action Control: notify.%s for rule '%s' failed",
            rule.notify_service,
            rule.name,
        )


async def async_run_watchdog(
    engine: ActionControlEngine,
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
    # Measured from function entry, before the lock: this reflects the real
    # elapsed time since the command was issued, including any time spent
    # queued behind a still-running check for the same (rule, entity).
    started_at = time.monotonic()
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
            _publish(
                engine, rule, status, RuleStatus.OK, hass.states.get(entity_id), started_at
            )
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
                _publish(
                    engine,
                    rule,
                    status,
                    RuleStatus.RETRYING,
                    hass.states.get(entity_id),
                    started_at,
                )
                await _reissue_command(engine, domain, service, entity_id, service_data)
                moved = await _wait_for_attribute_change(
                    hass, entity_id, rule.change_attribute, baseline, rule.change_timeout
                )
            final_state = hass.states.get(entity_id)
            if moved:
                _LOGGER.debug("Rule '%s': %s started moving, verified OK", rule.name, entity_id)
                _publish(engine, rule, status, RuleStatus.OK, final_state, started_at)
                return
            # No movement detected: this is the failure, independent of
            # whether change_attribute happens to be in attributes_to_check
            # (it usually isn't -- the cover preset relies on this check
            # instead of a snapshot tolerance comparison on position).
            current_value = (
                final_state.attributes.get(rule.change_attribute) if final_state else None
            )
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
                _publish(engine, rule, status, RuleStatus.RETRYING, final_state, started_at)
                await _reissue_command(engine, domain, service, entity_id, service_data)
                await asyncio.sleep(
                    _compute_retry_delay(rule.retry_delay, rule.retry_backoff, attempt)
                )
                final_state = hass.states.get(entity_id)
                result = comparator.compare(
                    expected_state, expected_attributes, rule.tolerances, final_state
                )
            if result.ok:
                _LOGGER.debug("Rule '%s': %s verified OK after retry", rule.name, entity_id)
                _publish(engine, rule, status, RuleStatus.OK, final_state, started_at)
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
                "Rule '%s': retries exhausted for %s, running escalation action",
                rule.name,
                entity_id,
            )
            await _run_escalation(engine, hass, rule)
            if rule.escalation_check_entity_id:
                await _verify_escalation(engine, hass, rule)
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
                "Rule '%s': retries exhausted for %s, escalation still in cooldown",
                rule.name,
                entity_id,
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
        _publish(engine, rule, status, rule_status, final_state, started_at)

        if rule.notify_persistent or rule.notify_service:
            message = _format_message(hass, entity_id, result, escalated)
            _LOGGER.debug("Rule '%s': sending failure notification for %s", rule.name, entity_id)
            await _notify(engine, rule, entity_id, message)


_FINAL_STATUSES = {RuleStatus.OK, RuleStatus.ESCALATED, RuleStatus.FAILED}


def _publish(
    engine: ActionControlEngine,
    rule: Rule,
    status: RuleRunStatus,
    outcome: RuleStatus,
    state: State | None,
    started_at: float,
) -> None:
    status.status = outcome
    status.actual_state = state.state if state else None
    status.actual_attributes = dict(state.attributes) if state else {}
    status.last_checked = datetime.now(UTC).isoformat()
    status.response_duration = time.monotonic() - started_at
    engine.set_status(rule.rule_id, status)

    if rule.log_entity_info and outcome in _FINAL_STATUSES:
        _LOGGER.info(
            "Rule '%s': %s -> %s in %.2fs (%s attempt(s))",
            rule.name,
            status.entity_id,
            outcome.value,
            status.response_duration,
            status.attempt,
        )
