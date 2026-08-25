"""The Action Control integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_ENGINE, DOMAIN, PLATFORMS
from .coordinator import ActionControlEngine
from .services import async_setup_services, async_unload_services


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Action Control from a config entry."""
    engine = ActionControlEngine(hass, entry)
    await engine.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_ENGINE: engine}
    await async_setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if data:
            await data[DATA_ENGINE].async_unload()
        if not hass.data.get(DOMAIN):
            async_unload_services(hass)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options (rules) change.

    A full reload is the simplest correct way to keep the rule table, the
    call_service listener, and the per-rule sensor entities in sync with the
    config-flow-managed rule list: it naturally adds/removes sensors for
    added/removed rules without needing separate live entity-management code.
    """
    await hass.config_entries.async_reload(entry.entry_id)
