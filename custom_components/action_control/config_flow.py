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
    EntitySelector,
    EntitySelectorConfig,
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
    def async_get_options_flow(config_entry: Any) -> ActionControlOptionsFlow:
        return ActionControlOptionsFlow()


class ActionControlOptionsFlow(OptionsFlow):
    """Menu-driven CRUD for watchdog rules and global settings."""

    def __init__(self) -> None:
        self._rules: dict[str, dict[str, Any]] = {}
        self._global: dict[str, Any] = {}
        self._editing_rule_id: str | None = None
        self._draft: dict[str, Any] = {}
        # True while the guided pass runs (adding a rule): steps chain into
        # one another. Once it is over, every step hands back to the menu.
        self._guided = False

    # ---- menu ----

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        self._rules = dict(self.config_entry.options.get(c.OPT_RULES, {}))
        self._global = dict(self.config_entry.options.get(c.OPT_GLOBAL, {}))
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "new_rule",
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

    # ---- the rule menu, and moving between its sections ----

    def _wizard_order(self) -> list[str]:
        """The sections this rule actually has, in order.

        The conditional ones are in it only when their gate is ticked, so
        neither the guided pass nor the menu ever offers a step that would
        have nothing to ask.
        """
        order = ["add_rule"]
        if self._exclusions_default():
            order.append("rule_exclude")
        order += ["add_rule_services", "rule_features", "rule_verify"]
        if self._draft.get(c.CONF_ESCALATION_ENABLED):
            order.append("rule_escalation")
            if self._escalation_check_default():
                order.append("rule_escalation_check")
        return order

    def _gate(self, key: str, *fields: str) -> bool:
        """Whether a wizard-only gate is on.

        Once the step that owns it has been submitted the answer is in the
        draft. Before that -- editing a rule, where the draft is the stored
        rule -- it has to come from the fields the gate maps onto, or the menu
        would hide a section the rule actually uses.
        """
        if key in self._draft:
            return bool(self._draft[key])
        return any(self._draft.get(field) for field in fields)

    def _exclusions_default(self) -> bool:
        return self._gate(
            c.CONF_EXCLUSIONS_ENABLED,
            c.CONF_ENTITY_ID_EXCLUDE,
            c.CONF_DEVICE_ID_EXCLUDE,
            c.CONF_ENTITY_ID_EXCLUDE_PATTERNS,
        )

    def _escalation_check_default(self) -> bool:
        return self._gate(
            c.CONF_ESCALATION_CHECK_ENABLED, c.CONF_ESCALATION_CHECK_ENTITY_ID
        )

    async def _after(self, step_id: str) -> Any:
        """Where to go once `step_id` has been filled in.

        During the guided pass that is simply the next section; afterwards,
        and whenever a section was opened from the menu, it is the menu. That
        is what replaces going back: nothing is ever more than two clicks
        away, and Home Assistant offers no back navigation of its own.
        """
        if self._guided:
            order = self._wizard_order()
            index = order.index(step_id)
            if index + 1 < len(order):
                return await getattr(self, f"async_step_{order[index + 1]}")()
            self._guided = False
        return await self.async_step_rule_menu()

    async def async_step_new_rule(self, user_input: dict[str, Any] | None = None) -> Any:
        """Start a brand new rule, walking every section once."""
        self._editing_rule_id = None
        self._draft = {}
        self._guided = True
        return await self.async_step_add_rule()

    async def async_step_rule_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """The rule's sections, as a menu, plus saving it."""
        return self.async_show_menu(
            step_id="rule_menu",
            menu_options=[*self._wizard_order(), "rule_save"],
            description_placeholders=self._menu_summaries(),
        )

    def _menu_summaries(self) -> dict[str, str]:
        """One line per section, shown under its button in the menu.

        Every key the translations reference must be here: a missing
        placeholder makes Home Assistant render the raw ``{name}``.
        """
        draft = self._draft
        excluded = sum(
            len(draft.get(key, []))
            for key in (
                c.CONF_ENTITY_ID_EXCLUDE,
                c.CONF_DEVICE_ID_EXCLUDE,
                c.CONF_ENTITY_ID_EXCLUDE_PATTERNS,
            )
        )
        services = draft.get(c.CONF_SERVICES) or []
        return {
            "name": draft.get(c.CONF_NAME) or "?",
            "domains": ", ".join(draft.get(c.CONF_DOMAINS, [])) or "?",
            "excluded": str(excluded),
            "services": ", ".join(services) if services else "*",
            "retries": str(draft.get(c.CONF_RETRIES, "?")),
            "escalation_check_entity_id": (
                draft.get(c.CONF_ESCALATION_CHECK_ENTITY_ID) or "?"
            ),
        }

    async def async_step_rule_save(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        return self._finalize_rule()

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
            # Straight to the menu: changing one field should not mean walking
            # every section again.
            self._guided = False
            return await self.async_step_rule_menu()
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

    # ---- add / edit: inclusion ----

    async def async_step_add_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
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
                    c.CONF_EXCLUSIONS_ENABLED: user_input[c.CONF_EXCLUSIONS_ENABLED],
                }
            )
            # vol.Required only guarantees the key is present, not that the
            # list has anything in it -- a rule with no domain never matches.
            if self._draft[c.CONF_DOMAINS]:
                if not self._draft[c.CONF_EXCLUSIONS_ENABLED]:
                    # Unticking it clears what was excluded, rather than
                    # leaving a filter in force that nothing shows any more.
                    self._draft.update(
                        {
                            c.CONF_ENTITY_ID_EXCLUDE: [],
                            c.CONF_DEVICE_ID_EXCLUDE: [],
                            c.CONF_ENTITY_ID_EXCLUDE_PATTERNS: [],
                        }
                    )
                return await self._after("add_rule")
            errors[c.CONF_DOMAINS] = "domains_required"

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
                vol.Required(
                    c.CONF_EXCLUSIONS_ENABLED, default=self._exclusions_default()
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="add_rule", data_schema=schema, errors=errors)

    # ---- add / edit: exclusion ----

    async def async_step_rule_exclude(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Entities and devices this rule must never watch."""
        if user_input is not None:
            self._draft.update(
                {
                    c.CONF_ENTITY_ID_EXCLUDE: user_input.get(
                        c.CONF_ENTITY_ID_EXCLUDE, []
                    ),
                    c.CONF_DEVICE_ID_EXCLUDE: user_input.get(
                        c.CONF_DEVICE_ID_EXCLUDE, []
                    ),
                    c.CONF_ENTITY_ID_EXCLUDE_PATTERNS: user_input.get(
                        c.CONF_ENTITY_ID_EXCLUDE_PATTERNS, []
                    ),
                }
            )
            return await self._after("rule_exclude")

        domains = self._draft.get(c.CONF_DOMAINS, [])
        # The picker is limited to the domains the rule watches -- that is what
        # makes it readable -- so an exclusion left over from a wider version of
        # the rule has to go, or the form would refuse to submit. It matched
        # nothing anyway: an entity outside those domains is already out.
        excluded = [
            entity_id
            for entity_id in self._draft.get(c.CONF_ENTITY_ID_EXCLUDE, [])
            if entity_id.split(".", 1)[0] in domains
        ]
        schema = vol.Schema(
            {
                vol.Optional(
                    c.CONF_ENTITY_ID_EXCLUDE, default=excluded
                ): EntitySelector(
                    EntitySelectorConfig(domain=domains, multiple=True)
                ),
                vol.Optional(
                    c.CONF_DEVICE_ID_EXCLUDE,
                    default=self._draft.get(c.CONF_DEVICE_ID_EXCLUDE, []),
                ): DeviceSelector(DeviceSelectorConfig(multiple=True)),
                vol.Optional(
                    c.CONF_ENTITY_ID_EXCLUDE_PATTERNS,
                    default=self._draft.get(c.CONF_ENTITY_ID_EXCLUDE_PATTERNS, []),
                ): SelectSelector(
                    SelectSelectorConfig(options=[], multiple=True, custom_value=True)
                ),
            }
        )
        return self.async_show_form(
            step_id="rule_exclude",
            data_schema=schema,
            description_placeholders={"domains": ", ".join(domains)},
        )

    # ---- add / edit: services, now that the domains are known ----

    async def async_step_add_rule_services(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._draft[c.CONF_SERVICES] = user_input.get(c.CONF_SERVICES, [])
            return await self._after("add_rule_services")

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

    # ---- add / edit: behavior -- which capabilities this rule uses ----

    def _preset_for_draft(self) -> dict[str, Any]:
        """Domain defaults, when the rule watches exactly one known domain."""
        domains = self._draft.get(c.CONF_DOMAINS, [])
        if len(domains) == 1 and domains[0] in DOMAIN_PRESETS:
            return DOMAIN_PRESETS[domains[0]]
        return {}

    async def async_step_rule_features(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Pick the capabilities, so later steps only ask what's relevant."""
        if user_input is not None:
            mode = user_input[c.CONF_VERIFICATION_MODE]
            self._draft.update(
                {
                    c.CONF_VERIFICATION_MODE: mode,
                    c.CONF_WAIT_FOR_CHANGE: mode == c.VERIFICATION_MODE_MOVEMENT,
                    c.CONF_ESCALATION_ENABLED: user_input[c.CONF_ESCALATION_ENABLED],
                    c.CONF_LOG_ENTITY_INFO: user_input[c.CONF_LOG_ENTITY_INFO],
                    c.CONF_NOTIFY_PERSISTENT: user_input[c.CONF_NOTIFY_PERSISTENT],
                    c.CONF_NOTIFY_SERVICE: user_input.get(c.CONF_NOTIFY_SERVICE) or None,
                }
            )
            return await self._after("rule_features")

        preset = self._preset_for_draft()
        wait_for_change = self._draft.get(
            c.CONF_WAIT_FOR_CHANGE, preset.get("wait_for_change", False)
        )
        notify_services = sorted(self.hass.services.async_services().get("notify", {}))
        schema = vol.Schema(
            {
                vol.Required(
                    c.CONF_VERIFICATION_MODE,
                    default=(
                        c.VERIFICATION_MODE_MOVEMENT
                        if wait_for_change
                        else c.VERIFICATION_MODE_DELAY
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(c.VERIFICATION_MODES),
                        translation_key=c.CONF_VERIFICATION_MODE,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    c.CONF_ESCALATION_ENABLED,
                    default=self._draft.get(c.CONF_ESCALATION_ENABLED, False),
                ): BooleanSelector(),
                vol.Required(
                    c.CONF_LOG_ENTITY_INFO,
                    default=self._draft.get(
                        c.CONF_LOG_ENTITY_INFO, c.DEFAULT_LOG_ENTITY_INFO
                    ),
                ): BooleanSelector(),
                vol.Required(
                    c.CONF_NOTIFY_PERSISTENT,
                    default=self._draft.get(c.CONF_NOTIFY_PERSISTENT, True),
                ): BooleanSelector(),
                vol.Optional(
                    c.CONF_NOTIFY_SERVICE,
                    default=self._draft.get(c.CONF_NOTIFY_SERVICE) or "",
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=notify_services,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="rule_features", data_schema=schema)

    # ---- add / edit: verification ----

    async def async_step_rule_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
        tolerances_text: str | None = None
        movement = (
            self._draft.get(c.CONF_VERIFICATION_MODE) == c.VERIFICATION_MODE_MOVEMENT
        )

        if user_input is not None:
            tolerances, invalid = _parse_tolerances(user_input.get(c.CONF_TOLERANCES))
            self._draft.update(
                {
                    c.CONF_ATTRIBUTES_TO_CHECK: user_input.get(
                        c.CONF_ATTRIBUTES_TO_CHECK, []
                    ),
                    c.CONF_TOLERANCES: tolerances,
                    c.CONF_RETRIES: user_input[c.CONF_RETRIES],
                    c.CONF_RETRY_DELAY: user_input[c.CONF_RETRY_DELAY],
                    c.CONF_RETRY_BACKOFF: user_input[c.CONF_RETRY_BACKOFF],
                }
            )
            if movement:
                change_attribute = user_input.get(c.CONF_CHANGE_ATTRIBUTE) or None
                self._draft.update(
                    {
                        c.CONF_CHANGE_ATTRIBUTE: change_attribute,
                        c.CONF_CHANGE_TIMEOUT: user_input[c.CONF_CHANGE_TIMEOUT],
                    }
                )
                # Movement mode is a no-op without it: the rule would silently
                # fall back to a snapshot comparison.
                if not change_attribute:
                    errors[c.CONF_CHANGE_ATTRIBUTE] = "change_attribute_required"
            else:
                self._draft[c.CONF_CHECK_DELAY] = user_input[c.CONF_CHECK_DELAY]
            if invalid:
                errors[c.CONF_TOLERANCES] = "invalid_tolerances"
                tolerances_text = user_input.get(c.CONF_TOLERANCES) or ""
            if not errors:
                return await self._after("rule_verify")

        preset = self._preset_for_draft()
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
        fields: dict[Any, Any] = {}
        if not movement:
            fields[
                vol.Required(
                    c.CONF_CHECK_DELAY,
                    default=self._draft.get(c.CONF_CHECK_DELAY, c.DEFAULT_CHECK_DELAY),
                )
            ] = NumberSelector(
                NumberSelectorConfig(min=0, max=120, step=0.5, mode=NumberSelectorMode.BOX)
            )
        fields.update(
            {
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
                    c.CONF_RETRY_BACKOFF,
                    default=self._draft.get(c.CONF_RETRY_BACKOFF, c.DEFAULT_RETRY_BACKOFF),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(c.RETRY_BACKOFF_MODES),
                        translation_key=c.CONF_RETRY_BACKOFF,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        if movement:
            fields.update(
                {
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
            step_id="rule_verify", data_schema=vol.Schema(fields), errors=errors
        )

    # ---- add / edit: recovery (only when it is enabled) ----

    async def async_step_rule_escalation(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._draft.update(
                {
                    c.CONF_ESCALATION_ACTION: user_input.get(c.CONF_ESCALATION_ACTION)
                    or None,
                    c.CONF_ESCALATION_COOLDOWN: user_input[c.CONF_ESCALATION_COOLDOWN],
                    c.CONF_ESCALATION_REPLAY_DELAY: user_input[
                        c.CONF_ESCALATION_REPLAY_DELAY
                    ],
                    c.CONF_ESCALATION_CHECK_ENABLED: user_input[
                        c.CONF_ESCALATION_CHECK_ENABLED
                    ],
                }
            )
            if not user_input[c.CONF_ESCALATION_CHECK_ENABLED]:
                self._draft[c.CONF_ESCALATION_CHECK_ENTITY_ID] = None
            return await self._after("rule_escalation")

        schema = vol.Schema(
            {
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
                    c.CONF_ESCALATION_CHECK_ENABLED,
                    default=self._escalation_check_default(),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="rule_escalation", data_schema=schema)

    # ---- add / edit: recovery check ----

    async def async_step_rule_escalation_check(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}

        if user_input is not None:
            entity_id = user_input.get(c.CONF_ESCALATION_CHECK_ENTITY_ID) or None
            state = user_input.get(c.CONF_ESCALATION_CHECK_STATE) or None
            self._draft.update(
                {
                    c.CONF_ESCALATION_CHECK_ENTITY_ID: entity_id,
                    c.CONF_ESCALATION_CHECK_STATE: state,
                    c.CONF_ESCALATION_CHECK_DELAY: user_input[
                        c.CONF_ESCALATION_CHECK_DELAY
                    ],
                }
            )
            # Both are needed, otherwise the check silently does nothing.
            if entity_id and state:
                return await self._after("rule_escalation_check")
            if not entity_id:
                errors[c.CONF_ESCALATION_CHECK_ENTITY_ID] = "escalation_check_required"
            if not state:
                errors[c.CONF_ESCALATION_CHECK_STATE] = "escalation_check_required"

        # EntitySelector rejects "" as an entity id, so it can't use a plain
        # `default=""` like the other optional fields here -- prefill it via
        # `suggested_value` instead, which skips voluptuous validation.
        check_entity_key = vol.Optional(
            c.CONF_ESCALATION_CHECK_ENTITY_ID,
            description={
                "suggested_value": self._draft.get(c.CONF_ESCALATION_CHECK_ENTITY_ID)
            },
        )
        schema = vol.Schema(
            {
                check_entity_key: EntitySelector(),
                vol.Optional(
                    c.CONF_ESCALATION_CHECK_STATE,
                    default=self._draft.get(c.CONF_ESCALATION_CHECK_STATE) or "",
                ): TextSelector(),
                vol.Required(
                    c.CONF_ESCALATION_CHECK_DELAY,
                    default=self._draft.get(
                        c.CONF_ESCALATION_CHECK_DELAY, c.DEFAULT_ESCALATION_CHECK_DELAY
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=0, max=600, step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(
            step_id="rule_escalation_check", data_schema=schema, errors=errors
        )

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
