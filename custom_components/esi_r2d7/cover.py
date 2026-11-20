"""Native blinds with individual Open/Close, universal Stop for R2D7 v4.1."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import (
    CONF_FIRST_CHANNEL,
    CONF_LAST_CHANNEL,
    CONF_SYSTEM,
    DOMAIN,
    RECENT_MOVE_SECONDS,
    R2D7ConfigEntry,
)
from .protocol import CONNECT_ERRORS, R2D7Connection

COMMAND_OPEN = "open"
COMMAND_CLOSED = "closed"
COMMAND_STOPPED = "stopped"
_COMMANDS = (COMMAND_OPEN, COMMAND_CLOSED, COMMAND_STOPPED)


def stopped_signal(entry_id: str) -> str:
    """Dispatcher signal carrying the set of channels a Stop applies to."""
    return f"{DOMAIN}_{entry_id}_stopped"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: R2D7ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    connection = entry.runtime_data
    async_add_entities(
        R2D7Blind(connection, entry, channel)
        for channel in range(
            entry.data[CONF_FIRST_CHANNEL], entry.data[CONF_LAST_CHANNEL] + 1
        )
    )


class _LastCommand(ExtraStoredData):
    """Kept across restarts and link drops, independent of availability."""

    def __init__(self, last_command: str | None) -> None:
        self.last_command = last_command

    def as_dict(self) -> dict[str, Any]:
        return {"last_command": self.last_command}


class R2D7Blind(CoverEntity, RestoreEntity):
    """Channel-addressed blind; no position feedback is provided by R2D7.

    The state is the LAST COMMAND SENT, never a measured position:
    Open -> open, Close -> closed, Stop -> open (a blind stopped short of
    closed counts as open in Home Assistant). `last_command` holds the word.
    """

    _attr_device_class = CoverDeviceClass.BLIND
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "blind"
    # The state is assumed from commands: keep every direction button usable.
    _attr_assumed_state = True
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(
        self, connection: R2D7Connection, entry: R2D7ConfigEntry, channel: int
    ) -> None:
        self._connection = connection
        self._channel = channel
        self._system = entry.data[CONF_SYSTEM]
        self._stopped_signal = stopped_signal(entry.entry_id)
        self._last_command: str | None = None
        self._attr_name = f"Channel {channel:02d}"
        self._attr_unique_id = (
            f"{entry.entry_id}_system_{self._system}_channel_{channel:02d}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESI R2D7 System {self._system}",
            manufacturer="ESI",
            model="R2D7",
        )

    @property
    def available(self) -> bool:
        """Follow the serial link, the only thing this integration can observe."""
        return self._connection.connected

    @property
    def is_closed(self) -> bool | None:
        """Derived from the last command only; unknown until one is sent."""
        if self._last_command is None:
            return None
        return self._last_command == COMMAND_CLOSED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_command": self._last_command,
            # Stop on this blind is not limited to this blind.
            "stop_scope": f"all blinds on system {self._system}",
        }

    @property
    def extra_restore_state_data(self) -> _LastCommand:
        return _LastCommand(self._last_command)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (extra := await self.async_get_last_extra_data()) is not None:
            restored = extra.as_dict().get("last_command")
            if restored in _COMMANDS:
                self._last_command = restored
        self.async_on_remove(
            self._connection.add_listener(self.async_write_ha_state)
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._stopped_signal, self._handle_stopped
            )
        )

    @callback
    def _set_last_command(self, value: str) -> None:
        self._last_command = value
        self.async_write_ha_state()

    @callback
    def _handle_stopped(self, channels: set[int]) -> None:
        if self._channel in channels:
            self._set_last_command(COMMAND_STOPPED)

    async def _send(self, action: Awaitable[None]) -> None:
        try:
            await action
        except CONNECT_ERRORS:
            raise HomeAssistantError(
                "ESI R2D7 serial link failed; the command may not have reached "
                "the controller"
            ) from None  # the cause can carry the port URL and its key

    async def async_open_cover(self, **kwargs: object) -> None:
        await self._send(self._connection.open_blind(self._channel))
        self._set_last_command(COMMAND_OPEN)

    async def async_close_cover(self, **kwargs: object) -> None:
        await self._send(self._connection.close_blind(self._channel))
        self._set_last_command(COMMAND_CLOSED)

    async def async_stop_cover(self, **kwargs: object) -> None:
        """WARNING: Stops ALL moving blinds in the system, not this channel."""
        await self._send(self._connection.stop_system())
        # The Stop reached every blind. Mark this one, plus any that were
        # recently told to move and so may have been caught mid-travel.
        channels = self._connection.take_recently_moved(RECENT_MOVE_SECONDS)
        async_dispatcher_send(
            self.hass, self._stopped_signal, channels | {self._channel}
        )
