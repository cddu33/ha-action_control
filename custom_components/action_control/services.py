"""Services: run_rule (on-demand test) and reset_escalation_cooldown."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_ENTITY_ID,
    ATTR_RULE_SENSOR,
    ATTR_SERVICE_DATA,
    DATA_ENGINE,
    DOMAIN,
    SERVICE_RESET_ESCALATION_COOLDOWN,
    SERVICE_RUN_RULE,
)
from .coordinator import ActionControlEngine
from .models import Rule

RUN_RULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_RULE_SENSOR): cv.entity_id,
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_SERVICE_DATA, default=dict): dict,
    }
)
RESET_ESCALATION_COOLDOWN_SCHEMA = vol.Schema({vol.Required(ATTR_RULE_SENSOR): cv.entity_id})


def _resolve_rule(
    hass: HomeAssistant, rule_sensor_entity_id: str
) -> tuple[ActionControlEngine, Rule]:
    """Find the rule behind a rule status sensor's entity id."""
    entity_entry = er.async_get(hass).async_get(rule_sensor_entity_id)
    if (
        entity_entry is None
        or entity_entry.platform != DOMAIN
        or entity_entry.config_entry_id is None
    ):
        raise ServiceValidationError(
            f"{rule_sensor_entity_id} is not an Action Control rule sensor"
        )
    entry_data = hass.data.get(DOMAIN, {}).get(entity_entry.config_entry_id)
    if entry_data is None:
        raise ServiceValidationError("Action Control is not set up")
    engine: ActionControlEngine = entry_data[DATA_ENGINE]
    rule_id = entity_entry.unique_id.removeprefix(f"{entity_entry.config_entry_id}_")
    rule = engine.rules.get(rule_id)
    if rule is None:
        raise ServiceValidationError(f"Rule {rule_id} no longer exists")
    return engine, rule


async def _async_run_rule(hass: HomeAssistant, call: ServiceCall) -> None:
    _engine, rule = _resolve_rule(hass, call.data[ATTR_RULE_SENSOR])
    target_entity_id: str = call.data[ATTR_ENTITY_ID]
    target_domain = target_entity_id.split(".", 1)[0]
    if target_domain not in rule.domains:
        raise ServiceValidationError(
            f"{target_entity_id} is not in rule '{rule.name}''s domains ({', '.join(rule.domains)})"
        )
    service_data = dict(call.data[ATTR_SERVICE_DATA])
    service = service_data.pop("service", None) or (rule.services[0] if rule.services else None)
    if not service:
        raise ServiceValidationError(
            f"Rule '{rule.name}' has no configured service; pass one in service_data"
        )
    # A real (non self-issued) call: the existing call_service listener picks
    # it up and runs the rule exactly as it would for a real user action.
    await hass.services.async_call(
        target_domain, service, service_data, target={"entity_id": target_entity_id}
    )


async def _async_reset_escalation_cooldown(hass: HomeAssistant, call: ServiceCall) -> None:
    engine, rule = _resolve_rule(hass, call.data[ATTR_RULE_SENSOR])
    engine.clear_escalation_cooldown(rule.rule_id)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the domain-level services (idempotent across config entry reloads)."""
    if hass.services.has_service(DOMAIN, SERVICE_RUN_RULE):
        return

    async def run_rule(call: ServiceCall) -> None:
        await _async_run_rule(hass, call)

    async def reset_escalation_cooldown(call: ServiceCall) -> None:
        await _async_reset_escalation_cooldown(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_RUN_RULE, run_rule, schema=RUN_RULE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_ESCALATION_COOLDOWN,
        reset_escalation_cooldown,
        schema=RESET_ESCALATION_COOLDOWN_SCHEMA,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, SERVICE_RUN_RULE)
    hass.services.async_remove(DOMAIN, SERVICE_RESET_ESCALATION_COOLDOWN)
