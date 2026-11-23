"""One honest control per R2D7 system: Stop ALL blinds."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SYSTEM, DOMAIN, RECENT_MOVE_SECONDS, R2D7ConfigEntry
from .cover import stopped_signal
from esi_r2d7 import CONNECT_ERRORS, R2D7Connection


async def async_setup_entry(
    hass: HomeAssistant,
    entry: R2D7ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([R2D7StopAllButton(entry.runtime_data, entry)])


class R2D7StopAllButton(ButtonEntity):
    """Sends the universal Stop. Same effect as Stop on any single blind."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Stop ALL blinds"
    _attr_icon = "mdi:stop-circle"

    def __init__(self, connection: R2D7Connection, entry: R2D7ConfigEntry) -> None:
        self._connection = connection
        system = entry.data[CONF_SYSTEM]
        self._stopped_signal = stopped_signal(entry.entry_id)
        self._attr_unique_id = f"{entry.entry_id}_system_{system}_stop_all"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESI R2D7 System {system}",
            manufacturer="ESI",
            model="R2D7",
        )

    @property
    def available(self) -> bool:
        return self._connection.connected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._connection.add_listener(self.async_write_ha_state)
        )

    async def async_press(self) -> None:
        try:
            await self._connection.stop_system()
        except CONNECT_ERRORS:
            raise HomeAssistantError(
                "ESI R2D7 serial link failed; the command may not have reached "
                "the controller"
            ) from None  # the cause can carry the port URL and its key
        # Mark the blinds that were recently told to move as stopped.
        async_dispatcher_send(
            self.hass,
            self._stopped_signal,
            self._connection.take_recently_moved(RECENT_MOVE_SECONDS),
        )
