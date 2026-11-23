"""ESI R2D7 native serial integration for Home Assistant."""

from __future__ import annotations

from datetime import datetime

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_DEVICE, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_call_later

from .const import CONF_SYSTEM, LOGGER, R2D7ConfigEntry
from esi_r2d7 import CONNECT_ERRORS, R2D7Connection

PLATFORMS = [Platform.BUTTON, Platform.COVER]

# Wait before reloading after a link loss so a flapping link cannot spin.
RELOAD_DELAY = 5


async def async_setup_entry(hass: HomeAssistant, entry: R2D7ConfigEntry) -> None:
    """Connect to R2D7; forward setup to the cover platform."""
    connection = R2D7Connection(entry.data[CONF_DEVICE], entry.data[CONF_SYSTEM])
    try:
        await connection.connect()
    except CONNECT_ERRORS:
        # Static message, no cause: the port URL can carry an ESPHome API key.
        raise ConfigEntryNotReady("Unable to open ESI R2D7 serial port") from None
    entry.runtime_data = connection

    cancel_reload: list = []

    @callback
    def _reload(_now: datetime) -> None:
        cancel_reload.clear()
        if entry.state is ConfigEntryState.SETUP_IN_PROGRESS:
            # Setup is still running: wait again instead of dropping the reload.
            # A failed setup unloads, and the unload cancels this timer.
            cancel_reload.append(async_call_later(hass, RELOAD_DELAY, _reload))
        elif entry.state is ConfigEntryState.LOADED:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    @callback
    def _on_link_change() -> None:
        # Reload on link loss; a failed reload becomes ConfigEntryNotReady and
        # Home Assistant then retries with its own backoff.
        if connection.connected or cancel_reload:
            return
        # A loss while platforms are still being set up must also reload;
        # _reload waits until the entry is LOADED.
        if entry.state not in (
            ConfigEntryState.LOADED,
            ConfigEntryState.SETUP_IN_PROGRESS,
        ):
            return
        LOGGER.warning("ESI R2D7 serial link lost; reloading in %s s", RELOAD_DELAY)
        cancel_reload.append(async_call_later(hass, RELOAD_DELAY, _reload))

    @callback
    def _cancel_pending_reload() -> None:
        while cancel_reload:
            cancel_reload.pop()()

    async def _on_stop(_event: Event) -> None:
        await connection.disconnect()

    entry.async_on_unload(connection.add_listener(_on_link_change))
    entry.async_on_unload(_cancel_pending_reload)
    # async_listen, not async_listen_once: a fired one-time listener cannot be
    # removed again when the entry unloads.
    entry.async_on_unload(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, _on_stop))

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await connection.disconnect()
        raise


async def async_unload_entry(hass: HomeAssistant, entry: R2D7ConfigEntry) -> None:
    """Disconnect from R2D7 after its entities unload."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
    await entry.runtime_data.disconnect()
