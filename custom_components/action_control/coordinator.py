"""The ActionControlEngine: listens to call_service events and dispatches
matching rules to the watchdog orchestrator."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from . import comparator, matching, watchdog
from .const import CONF_GLOBAL_ENABLED, DOMAIN, ISSUE_STALE_TARGET, OPT_GLOBAL, OPT_RULES
from .context_registry import SelfIssuedContexts
from .models import Rule, RuleRunStatus

_LOGGER = logging.getLogger(__name__)

SIGNAL_RULE_UPDATE = f"{DOMAIN}_rule_update"

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.escalation_cooldowns"
STORAGE_SAVE_DELAY = 5


class ActionControlEngine:
    """Owns the call_service listener, rule table, and per-rule run state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.rules: dict[str, Rule] = {}
        self.rule_status: dict[str, RuleRunStatus] = {}
        self.contexts = SelfIssuedContexts()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._escalation_cooldowns: dict[str, float] = {}
        self._run_tokens: dict[tuple[str, str], int] = {}
        self._tasks: set[asyncio.Task] = set()
        self._runs: dict[tuple[str, str], asyncio.Task] = {}
        self._unsub_listener: callback | None = None
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.load_rules()

    def load_rules(self) -> None:
        raw_rules = self.entry.options.get(OPT_RULES, {})
        self.rules = {
            rule_id: Rule.from_dict(data) for rule_id, data in raw_rules.items()
        }

    @property
    def enabled(self) -> bool:
        return self.entry.options.get(OPT_GLOBAL, {}).get(CONF_GLOBAL_ENABLED, True)

    def lock_for(self, rule_id: str, entity_id: str) -> asyncio.Lock:
        key = (rule_id, entity_id)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def next_run_token(self, rule_id: str, entity_id: str) -> int:
        """Claim the newest run for this (rule, entity)."""
        key = (rule_id, entity_id)
        token = self._run_tokens.get(key, 0) + 1
        self._run_tokens[key] = token
        return token

    def is_current_run(self, rule_id: str, entity_id: str, token: int) -> bool:
        return self._run_tokens.get((rule_id, entity_id)) == token

    def _cancel_previous_run(self, rule: Rule, entity_id: str) -> None:
        """Stop the check still running for this (rule, entity), if any.

        The newer command is the one that counts: a light asked to turn on,
        then off a few seconds later, must not keep being retried -- nor
        escalated, nor replayed -- towards "on" while the new check waits
        behind it for the lock.
        """
        previous = self._runs.pop((rule.rule_id, entity_id), None)
        if previous is not None and not previous.done():
            _LOGGER.debug(
                "Rule '%s': newer command for %s, cancelling the check still in progress",
                rule.name,
                entity_id,
            )
            previous.cancel()

    def _cancel_runs_overridden_by(
        self, domain: str, service: str, entities: set[str]
    ) -> None:
        """Cancel checks, from any rule, that this call has just overruled.

        A rule watching only light.turn_on still has to drop its check when
        the light is turned off: it would otherwise report the light as
        failing -- and retry, switching it back on. Only a call that sets
        the entity's state counts; one that merely tweaks it does not.
        """
        for key in list(self._runs):
            rule_id, entity_id = key
            if entity_id not in entities or entity_id.split(".", 1)[0] != domain:
                continue
            state = self.hass.states.get(entity_id)
            if comparator.expected_states_for(domain, service, state) is None:
                continue
            task = self._runs.pop(key)
            if not task.done():
                rule = self.rules.get(rule_id)
                _LOGGER.debug(
                    "Rule '%s': %s.%s overrides the check in progress on %s, cancelling it",
                    rule.name if rule else rule_id,
                    domain,
                    service,
                    entity_id,
                )
                task.cancel()

    def _forget_run(self, key: tuple[str, str], task: asyncio.Task) -> None:
        if self._runs.get(key) is task:
            del self._runs[key]

    # Cooldown deadlines are wall-clock epochs, not monotonic ones, so they
    # still mean something after a restart.
    def escalation_ready(self, rule_id: str) -> bool:
        return time.time() >= self._escalation_cooldowns.get(rule_id, 0)

    def arm_escalation_cooldown(self, rule_id: str, seconds: float) -> None:
        self._escalation_cooldowns[rule_id] = time.time() + seconds
        self._store.async_delay_save(self._cooldowns_to_save, STORAGE_SAVE_DELAY)

    def clear_escalation_cooldown(self, rule_id: str) -> None:
        if self._escalation_cooldowns.pop(rule_id, None) is not None:
            self._store.async_delay_save(self._cooldowns_to_save, STORAGE_SAVE_DELAY)

    def _cooldowns_to_save(self) -> dict[str, Any]:
        now = time.time()
        return {
            "cooldowns": {
                rule_id: deadline
                for rule_id, deadline in self._escalation_cooldowns.items()
                if deadline > now
            }
        }

    def set_status(self, rule_id: str, status: RuleRunStatus) -> None:
        self.rule_status[rule_id] = status
        async_dispatcher_send(self.hass, SIGNAL_RULE_UPDATE, rule_id)

    def _check_stale_targets(self) -> None:
        """Raise a repair issue for rules whose area/label/device target was deleted."""
        area_reg = ar.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        label_reg = lr.async_get(self.hass)
        for rule in self.rules.values():
            missing = (
                any(area_reg.async_get_area(a) is None for a in rule.area_ids)
                or any(dev_reg.async_get(d) is None for d in rule.device_ids)
                or any(label_reg.async_get_label(label) is None for label in rule.label_ids)
            )
            issue_id = f"{ISSUE_STALE_TARGET}_{rule.rule_id}"
            if missing:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_STALE_TARGET,
                    translation_placeholders={"rule": rule.name},
                )
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def async_setup(self) -> None:
        stored = await self._store.async_load()
        if stored:
            now = time.time()
            self._escalation_cooldowns = {
                rule_id: deadline
                for rule_id, deadline in (stored.get("cooldowns") or {}).items()
                if deadline > now
            }
        self._check_stale_targets()
        self._unsub_listener = self.hass.bus.async_listen(
            EVENT_CALL_SERVICE, self._handle_call_service
        )

    async def async_unload(self) -> None:
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    @callback
    def _handle_call_service(self, event: Event) -> None:
        if not self.enabled or not self.rules:
            return
        if event.context and self.contexts.is_self_issued(event.context.id):
            _LOGGER.debug(
                "Ignoring self-issued call_service event (context %s): %s.%s",
                event.context.id,
                event.data.get("domain"),
                event.data.get("service"),
            )
            return

        domain = event.data.get("domain")
        service = event.data.get("service")
        service_data: dict[str, Any] = dict(event.data.get("service_data") or {})
        if not domain or not service:
            return

        matching_rules = [
            rule
            for rule in self.rules.values()
            if rule.enabled and matching.rule_matches_service(rule, domain, service)
        ]
        if not matching_rules and not self._runs:
            return

        if matching_rules:
            _LOGGER.debug(
                "call_service %s.%s matches rule(s) %s, resolving targets from %s",
                domain,
                service,
                [rule.name for rule in matching_rules],
                service_data,
            )

        # Entity resolution and expected-state computation happen entirely
        # synchronously, in this same callback invocation, so that a
        # "toggle" call's expected outcome is derived from the state as it
        # was the instant the event fired -- not from a state that may have
        # already changed by the time an async-scheduled task got to run.
        entities = matching.resolve_target_entities(self.hass, service_data)
        # Before the matching rules start their own: a check whose rule does
        # not watch this service must still give way to it.
        if self._runs:
            self._cancel_runs_overridden_by(domain, service, entities)
        if not matching_rules:
            return
        if not entities:
            _LOGGER.debug("%s.%s resolved to no entities, nothing to watch", domain, service)
            return

        for rule in matching_rules:
            for entity_id in entities:
                if not matching.entity_matches_rule(self.hass, rule, entity_id):
                    continue
                current_state = self.hass.states.get(entity_id)
                if current_state is None:
                    _LOGGER.debug(
                        "Rule '%s': %s has no state, nothing to watch", rule.name, entity_id
                    )
                    continue
                expected_state, expected_attrs = comparator.compute_expected(
                    domain, service, service_data, rule.attributes_to_check, current_state
                )
                _LOGGER.debug(
                    "Rule '%s': watching %s after %s.%s (expected_state=%s, expected_attrs=%s)",
                    rule.name,
                    entity_id,
                    domain,
                    service,
                    comparator.format_expected_state(expected_state),
                    expected_attrs,
                )
                self._cancel_previous_run(rule, entity_id)
                task = self.hass.async_create_task(
                    watchdog.async_run_watchdog(
                        self,
                        rule,
                        entity_id,
                        domain,
                        service,
                        service_data,
                        expected_state,
                        expected_attrs,
                        self.next_run_token(rule.rule_id, entity_id),
                    ),
                    f"{DOMAIN}_watchdog_{rule.rule_id}_{entity_id}",
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
                key = (rule.rule_id, entity_id)
                self._runs[key] = task
                task.add_done_callback(lambda done, key=key: self._forget_run(key, done))
