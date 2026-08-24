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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from . import comparator, matching, watchdog
from .const import CONF_GLOBAL_ENABLED, DOMAIN, OPT_GLOBAL, OPT_RULES
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

    # Cooldown deadlines are wall-clock epochs, not monotonic ones, so they
    # still mean something after a restart.
    def escalation_ready(self, rule_id: str) -> bool:
        return time.time() >= self._escalation_cooldowns.get(rule_id, 0)

    def arm_escalation_cooldown(self, rule_id: str, seconds: float) -> None:
        self._escalation_cooldowns[rule_id] = time.time() + seconds
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

    async def async_setup(self) -> None:
        stored = await self._store.async_load()
        if stored:
            now = time.time()
            self._escalation_cooldowns = {
                rule_id: deadline
                for rule_id, deadline in (stored.get("cooldowns") or {}).items()
                if deadline > now
            }
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
        if not matching_rules:
            return

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
