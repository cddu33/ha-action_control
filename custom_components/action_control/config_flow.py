"""Config flow (single instance) and options flow (rule CRUD) for Action Control."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import translation
from homeassistant.helpers.selector import (
    ActionSelector,
    AreaSelector,
    AreaSelectorConfig,
    BooleanSelector,
    DeviceSelector,
    DeviceSelectorConfig,
    LabelSelector,
    LabelSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from . import const as c
from .domain_defaults import DOMAIN_PRESETS
from .models import Rule


def _parse_tolerances(text: str | None) -> tuple[dict[str, float], list[str]]:
    """Parse "attr:value, attr2:value2", also returning the unusable chunks."""
    result: dict[str, float] = {}
    invalid: list[str] = []
    for chunk in (text or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        attr, separator, value = chunk.partition(":")
        attr = attr.strip()
        if not separator or not attr:
            invalid.append(chunk)
            continue
        try:
            result[attr] = float(value.strip())
        except ValueError:
            invalid.append(chunk)
    return result, invalid


def _format_tolerances(tolerances: dict[str, float]) -> str:
    return ", ".join(f"{attr}:{value}" for attr, value in tolerances.items())


class ActionControlConfigFlow(ConfigFlow, domain=c.DOMAIN):
    """Handle the (single-instance) initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(
                title="Action Control",
                data={},
                options={
                    c.OPT_RULES: {},
                    c.OPT_GLOBAL: {c.CONF_GLOBAL_ENABLED: c.DEFAULT_GLOBAL_ENABLED},
                },
            )
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> "ActionControlOptionsFlow":
        return ActionControlOptionsFlow()


class ActionControlOptionsFlow(OptionsFlow):
    """Menu-driven CRUD for watchdog rules and global settings."""

    def __init__(self) -> None:
        self._rules: dict[str, dict[str, Any]] = {}
        self._global: dict[str, Any] = {}
        self._editing_rule_id: str | None = None
        self._draft: dict[str, Any] = {}

    # ---- menu ----

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        self._rules = dict(self.config_entry.options.get(c.OPT_RULES, {}))
        self._global = dict(self.config_entry.options.get(c.OPT_GLOBAL, {}))
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_rule",
                "edit_rule_select",
                "delete_rule_select",
                "global_settings",
            ],
        )

    def _save(self) -> Any:
        return self.async_create_entry(
            title="",
            data={c.OPT_RULES: self._rules, c.OPT_GLOBAL: self._global},
        )

    def _rule_choices(self) -> list[SelectOptionDict]:
        return [
            SelectOptionDict(
                value=rule_id,
                label=("" if data.get(c.CONF_ENABLED, True) else "⏸ ")
                + data.get(c.CONF_NAME, rule_id),
            )
            for rule_id, data in self._rules.items()
        ]

    def _remove_rule_entity(self, rule_id: str) -> None:
        """Drop the rule's sensor, which would otherwise linger as unavailable."""
        ent_reg = er.async_get(self.hass)
        entity_id = ent_reg.async_get_entity_id(
            Platform.SENSOR, c.DOMAIN, f"{self.config_entry.entry_id}_{rule_id}"
        )
        if entity_id:
            ent_reg.async_remove(entity_id)

    # ---- global settings ----

    async def async_step_global_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._global = {
                c.CONF_GLOBAL_ENABLED: user_input[c.CONF_GLOBAL_ENABLED],
                c.CONF_DEFAULT_RETRIES: user_input[c.CONF_DEFAULT_RETRIES],
                c.CONF_DEFAULT_RETRY_DELAY: user_input[c.CONF_DEFAULT_RETRY_DELAY],
            }
            return self._save()

        schema = vol.Schema(
            {
                vol.Required(
                    c.CONF_GLOBAL_ENABLED,
                    default=self._global.get(
                        c.CONF_GLOBAL_ENABLED, c.DEFAULT_GLOBAL_ENABLED
                    ),
                ): BooleanSelector(),
                vol.Required(
                    c.CONF_DEFAULT_RETRIES,
                    default=self._global.get(
                        c.CONF_DEFAULT_RETRIES, c.DEFAULT_RETRIES
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=10, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(
                    c.CONF_DEFAULT_RETRY_DELAY,
                    default=self._global.get(
                        c.CONF_DEFAULT_RETRY_DELAY, c.DEFAULT_RETRY_DELAY
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=600, step=0.5, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="global_settings", data_schema=schema)

    # ---- delete ----

    async def async_step_delete_rule_select(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if not self._rules:
            return self.async_abort(reason="no_rules")
        if user_input is not None:
            self._editing_rule_id = user_input["rule_id"]
            return await self.async_step_delete_rule_confirm()
        schema = vol.Schema(
            {
                vol.Required("rule_id"): SelectSelector(
                    SelectSelectorConfig(
                        options=self._rule_choices(), mode=SelectSelectorMode.DROPDOWN
                    )
                )
            }
        )
        return self.async_show_form(step_id="delete_rule_select", data_schema=schema)

    async def async_step_delete_rule_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            if user_input.get("confirm"):
                self._rules.pop(self._editing_rule_id, None)
                self._remove_rule_entity(self._editing_rule_id)
            return self._save()
        schema = vol.Schema({vol.Required("confirm", default=False): BooleanSelector()})
        rule_name = self._rules.get(self._editing_rule_id, {}).get(c.CONF_NAME, "")
        return self.async_show_form(
            step_id="delete_rule_confirm",
            data_schema=schema,
            description_placeholders={"rule_name": rule_name},
        )

    # ---- edit selection ----

    async def async_step_edit_rule_select(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if not self._rules:
            return self.async_abort(reason="no_rules")
        if user_input is not None:
            self._editing_rule_id = user_input["rule_id"]
            self._draft = dict(self._rules[self._editing_rule_id])
            return await self.async_step_add_rule()
        schema = vol.Schema(
            {
                vol.Required("rule_id"): SelectSelector(
                    SelectSelectorConfig(
                        options=self._rule_choices(), mode=SelectSelectorMode.DROPDOWN
                    )
                )
            }
        )
        return self.async_show_form(step_id="edit_rule_select", data_schema=schema)

    # ---- add / edit: page 1 - targeting ----

    async def async_step_add_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is None and self._editing_rule_id is None:
            self._draft = {}

        if user_input is not None:
            self._draft.update(
                {
                    c.CONF_NAME: user_input[c.CONF_NAME],
                    c.CONF_ENABLED: user_input.get(c.CONF_ENABLED, True),
                    c.CONF_DOMAINS: user_input[c.CONF_DOMAINS],
                    c.CONF_ENTITY_ID_PATTERN: user_input.get(c.CONF_ENTITY_ID_PATTERN) or None,
                    c.CONF_NAME_PATTERN: user_input.get(c.CONF_NAME_PATTERN) or None,
                    c.CONF_AREA_IDS: user_input.get(c.CONF_AREA_IDS, []),
                    c.CONF_LABEL_IDS: user_input.get(c.CONF_LABEL_IDS, []),
                    c.CONF_DEVICE_IDS: user_input.get(c.CONF_DEVICE_IDS, []),
                }
            )
            return await self.async_step_add_rule_services()

        known_domains = sorted(
            {entity_id.split(".", 1)[0] for entity_id in self.hass.states.async_entity_ids()}
        )
        # Show each domain under its name translated to the system's
        # configured language (e.g. "Lumière" instead of "light") when
        # Home Assistant has that translation, falling back to the raw
        # domain id for anything unknown/custom.
        titles = await translation.async_get_translations(
            self.hass, self.hass.config.language, "title", integrations=known_domains
        )
        domain_options = [
            SelectOptionDict(value=domain, label=titles.get(f"component.{domain}.title", domain))
            for domain in known_domains
        ]
        schema = vol.Schema(
            {
                vol.Required(c.CONF_NAME, default=self._draft.get(c.CONF_NAME, "")): str,
                vol.Required(
                    c.CONF_ENABLED, default=self._draft.get(c.CONF_ENABLED, True)
                ): BooleanSelector(),
                vol.Required(
                    c.CONF_DOMAINS, default=self._draft.get(c.CONF_DOMAINS, [])
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=domain_options,
                        multiple=True,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    c.CONF_ENTITY_ID_PATTERN,
                    default=self._draft.get(c.CONF_ENTITY_ID_PATTERN) or "",
                ): str,
                vol.Optional(
                    c.CONF_NAME_PATTERN, default=self._draft.get(c.CONF_NAME_PATTERN) or ""
                ): str,
                vol.Optional(
                    c.CONF_AREA_IDS, default=self._draft.get(c.CONF_AREA_IDS, [])
                ): AreaSelector(AreaSelectorConfig(multiple=True)),
                vol.Optional(
                    c.CONF_LABEL_IDS, default=self._draft.get(c.CONF_LABEL_IDS, [])
                ): LabelSelector(LabelSelectorConfig(multiple=True)),
                vol.Optional(
                    c.CONF_DEVICE_IDS, default=self._draft.get(c.CONF_DEVICE_IDS, [])
                ): DeviceSelector(DeviceSelectorConfig(multiple=True)),
            }
        )
        return self.async_show_form(step_id="add_rule", data_schema=schema)

    # ---- add / edit: page 1b - which services, now that domains are known ----

    async def async_step_add_rule_services(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._draft[c.CONF_SERVICES] = user_input.get(c.CONF_SERVICES, [])
            return await self.async_step_rule_verify()

        # Populated from the services Home Assistant actually registers for
        # the domain(s) just chosen (union across domains), so the picker
        # always offers real, existing services instead of an empty list --
        # custom_value still allows typing one that isn't registered yet.
        domains = self._draft.get(c.CONF_DOMAINS, [])
        available_services: set[str] = set()
        for domain in domains:
            available_services.update(self.hass.services.async_services().get(domain, {}))

        schema = vol.Schema(
            {
                vol.Optional(
                    c.CONF_SERVICES, default=self._draft.get(c.CONF_SERVICES, [])
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=sorted(available_services),
                        multiple=True,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="add_rule_services",
            data_schema=schema,
            description_placeholders={"domains": ", ".join(domains)},
        )

    # ---- add / edit: page 2 - verification ----

    async def async_step_rule_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
        tolerances_text: str | None = None

        if user_input is not None:
            tolerances, invalid = _parse_tolerances(user_input.get(c.CONF_TOLERANCES))
            self._draft.update(
                {
                    c.CONF_CHECK_DELAY: user_input[c.CONF_CHECK_DELAY],
                    c.CONF_ATTRIBUTES_TO_CHECK: user_input.get(
                        c.CONF_ATTRIBUTES_TO_CHECK, []
                    ),
                    c.CONF_TOLERANCES: tolerances,
                    c.CONF_RETRIES: user_input[c.CONF_RETRIES],
                    c.CONF_RETRY_DELAY: user_input[c.CONF_RETRY_DELAY],
                    c.CONF_WAIT_FOR_CHANGE: user_input[c.CONF_WAIT_FOR_CHANGE],
                    c.CONF_CHANGE_ATTRIBUTE: user_input.get(c.CONF_CHANGE_ATTRIBUTE)
                    or None,
                    c.CONF_CHANGE_TIMEOUT: user_input[c.CONF_CHANGE_TIMEOUT],
                }
            )
            if not invalid:
                return await self.async_step_rule_escalation()
            errors[c.CONF_TOLERANCES] = "invalid_tolerances"
            tolerances_text = user_input.get(c.CONF_TOLERANCES) or ""

        preset: dict[str, Any] = {}
        domains = self._draft.get(c.CONF_DOMAINS, [])
        if len(domains) == 1 and domains[0] in DOMAIN_PRESETS:
            preset = DOMAIN_PRESETS[domains[0]]

        default_attrs = self._draft.get(
            c.CONF_ATTRIBUTES_TO_CHECK, preset.get("attributes_to_check", [])
        )
        default_tolerances = self._draft.get(
            c.CONF_TOLERANCES, preset.get("tolerances", {})
        )
        if tolerances_text is None:
            tolerances_text = _format_tolerances(default_tolerances)
        # A brand new rule starts from the global defaults.
        default_retries = self._draft.get(
            c.CONF_RETRIES, self._global.get(c.CONF_DEFAULT_RETRIES, c.DEFAULT_RETRIES)
        )
        default_retry_delay = self._draft.get(
            c.CONF_RETRY_DELAY,
            self._global.get(c.CONF_DEFAULT_RETRY_DELAY, c.DEFAULT_RETRY_DELAY),
        )
        schema = vol.Schema(
            {
                vol.Required(
                    c.CONF_CHECK_DELAY,
                    default=self._draft.get(c.CONF_CHECK_DELAY, c.DEFAULT_CHECK_DELAY),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=120, step=0.5, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    c.CONF_ATTRIBUTES_TO_CHECK, default=default_attrs
                ): SelectSelector(
                    SelectSelectorConfig(options=[], multiple=True, custom_value=True)
                ),
                vol.Optional(
                    c.CONF_TOLERANCES, default=tolerances_text
                ): TextSelector(),
                vol.Required(
                    c.CONF_RETRIES, default=default_retries
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=10, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(
                    c.CONF_RETRY_DELAY,
                    default=default_retry_delay,
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=600, step=0.5, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(
                    c.CONF_WAIT_FOR_CHANGE,
                    default=self._draft.get(
                        c.CONF_WAIT_FOR_CHANGE, preset.get("wait_for_change", False)
                    ),
                ): BooleanSelector(),
                vol.Optional(
                    c.CONF_CHANGE_ATTRIBUTE,
                    default=self._draft.get(c.CONF_CHANGE_ATTRIBUTE)
                    or preset.get("change_attribute")
                    or "",
                ): str,
                vol.Required(
                    c.CONF_CHANGE_TIMEOUT,
                    default=self._draft.get(
                        c.CONF_CHANGE_TIMEOUT,
                        preset.get("change_timeout", c.DEFAULT_CHANGE_TIMEOUT),
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=1, max=600, step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(
            step_id="rule_verify", data_schema=schema, errors=errors
        )

    # ---- add / edit: page 3 - escalation & notifications ----

    async def async_step_rule_escalation(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._draft.update(
                {
                    c.CONF_ESCALATION_ENABLED: user_input[c.CONF_ESCALATION_ENABLED],
                    c.CONF_ESCALATION_ACTION: user_input.get(c.CONF_ESCALATION_ACTION)
                    or None,
                    c.CONF_ESCALATION_COOLDOWN: user_input[c.CONF_ESCALATION_COOLDOWN],
                    c.CONF_ESCALATION_REPLAY_DELAY: user_input[
                        c.CONF_ESCALATION_REPLAY_DELAY
                    ],
                    c.CONF_NOTIFY_PERSISTENT: user_input[c.CONF_NOTIFY_PERSISTENT],
                    c.CONF_NOTIFY_SERVICE: user_input.get(c.CONF_NOTIFY_SERVICE) or None,
                }
            )
            return self._finalize_rule()

        notify_services = sorted(self.hass.services.async_services().get("notify", {}))
        schema = vol.Schema(
            {
                vol.Required(
                    c.CONF_ESCALATION_ENABLED,
                    default=self._draft.get(c.CONF_ESCALATION_ENABLED, False),
                ): BooleanSelector(),
                vol.Optional(
                    c.CONF_ESCALATION_ACTION,
                    default=self._draft.get(c.CONF_ESCALATION_ACTION) or [],
                ): ActionSelector(),
                vol.Required(
                    c.CONF_ESCALATION_COOLDOWN,
                    default=self._draft.get(
                        c.CONF_ESCALATION_COOLDOWN, c.DEFAULT_ESCALATION_COOLDOWN
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=86400, step=30, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(
                    c.CONF_ESCALATION_REPLAY_DELAY,
                    default=self._draft.get(
                        c.CONF_ESCALATION_REPLAY_DELAY, c.DEFAULT_ESCALATION_REPLAY_DELAY
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=3600, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(
                    c.CONF_NOTIFY_PERSISTENT,
                    default=self._draft.get(c.CONF_NOTIFY_PERSISTENT, True),
                ): BooleanSelector(),
                vol.Optional(
                    c.CONF_NOTIFY_SERVICE,
                    default=self._draft.get(c.CONF_NOTIFY_SERVICE) or "",
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=notify_services, custom_value=True, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
            }
        )
        return self.async_show_form(step_id="rule_escalation", data_schema=schema)

    def _finalize_rule(self) -> Any:
        rule_id = self._editing_rule_id
        if rule_id and rule_id in self._rules:
            rule = Rule.from_dict({**self._rules[rule_id], **self._draft, c.CONF_RULE_ID: rule_id})
        else:
            rule = Rule.from_dict(self._draft)
        self._rules[rule.rule_id] = rule.to_dict()
        self._editing_rule_id = None
        self._draft = {}
        return self._save()
