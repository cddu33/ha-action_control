"""Per-rule status sensor: ok / retrying / escalated / failed / idle."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .const import DATA_ENGINE, DOMAIN
from .coordinator import SIGNAL_RULE_UPDATE, ActionControlEngine
from .entity import ActionControlEntity
from .models import Rule, RuleRunStatus, RuleStatus

_ICONS = {
    RuleStatus.IDLE: "mdi:timer-sand-empty",
    RuleStatus.OK: "mdi:check-circle",
    RuleStatus.RETRYING: "mdi:refresh",
    RuleStatus.ESCALATED: "mdi:alert",
    RuleStatus.FAILED: "mdi:alert-circle",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one status sensor per configured rule."""
    engine: ActionControlEngine = hass.data[DOMAIN][entry.entry_id][DATA_ENGINE]
    async_add_entities(
        RuleStatusSensor(entry, engine, rule) for rule in engine.rules.values()
    )


class RuleStatusSensor(ActionControlEntity, SensorEntity):
    """Surfaces the latest watchdog outcome for one rule."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    # An enum sensor, so the states are translatable.
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [status.value for status in RuleStatus]

    def __init__(self, entry: ConfigEntry, engine: ActionControlEngine, rule: Rule) -> None:
        super().__init__(entry)
        self._engine = engine
        self._rule = rule
        self._attr_unique_id = f"{entry.entry_id}_{rule.rule_id}"
        # translation_key drives the state translations, not the name.
        self._attr_translation_key = "rule_status"
        self._attr_name = rule.name

    @property
    def _status(self) -> RuleRunStatus:
        return self._engine.rule_status.get(self._rule.rule_id, RuleRunStatus())

    @property
    def native_value(self) -> str:
        return self._status.status.value

    @property
    def icon(self) -> str:
        return _ICONS.get(self._status.status, "mdi:help-circle")

    @property
    def extra_state_attributes(self) -> dict:
        status = self._status
        return {
            "entity_id": status.entity_id,
            "expected_state": status.expected_state,
            "expected_attributes": status.expected_attributes,
            "actual_state": status.actual_state,
            "actual_attributes": status.actual_attributes,
            "attempt": status.attempt,
            "mismatches": status.mismatches,
            "last_checked": status.last_checked,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_RULE_UPDATE, self._handle_update
            )
        )

    @callback
    def _handle_update(self, rule_id: str) -> None:
        if rule_id == self._rule.rule_id:
            self.async_write_ha_state()
