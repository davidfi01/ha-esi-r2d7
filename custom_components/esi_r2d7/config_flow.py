"""UI setup for ESI R2D7, following Home Assistant's Denon RS-232 pattern."""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_DEVICE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import SerialPortSelector

from .const import (
    CONF_FIRST_CHANNEL,
    CONF_LAST_CHANNEL,
    CONF_SYSTEM,
    DEFAULT_FIRST_CHANNEL,
    DEFAULT_LAST_CHANNEL,
    DEFAULT_SYSTEM,
    DOMAIN,
    LOGGER,
)
from esi_r2d7 import CONNECT_ERRORS, R2D7Connection


class EsiR2D7ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure one R2D7 system through an AUX-2 or local serial port."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            port = user_input[CONF_DEVICE]
            system = user_input[CONF_SYSTEM]
            first = user_input[CONF_FIRST_CHANNEL]
            last = user_input[CONF_LAST_CHANNEL]
            if first > last:
                errors["base"] = "invalid_channel_range"
            else:
                # A proxy serial port should have only one consuming connection.
                # Grouped R2D7 systems on a single bus require a shared hub.
                self._async_abort_entries_match({CONF_DEVICE: port})
                connection = R2D7Connection(port, system)
                try:
                    await connection.connect()
                except (*CONNECT_ERRORS, HomeAssistantError):
                    # HomeAssistantError: the usb stub raises ConfigEntryNotReady
                    # while the ESPHome integration has not loaded.
                    errors["base"] = "cannot_connect"
                except Exception as err:  # noqa: BLE001
                    # Only the type: a traceback can carry the port URL and its key.
                    LOGGER.error(
                        "Unexpected error opening the serial port: %s",
                        type(err).__name__,
                    )
                    errors["base"] = "unknown"
                else:
                    await connection.disconnect()
                    return self.async_create_entry(
                        title=f"ESI R2D7 System {system}", data=user_input
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_DEVICE): SerialPortSelector(),
                        probatio.Required(
                            CONF_SYSTEM, default=DEFAULT_SYSTEM
                        ): probatio.All(
                            probatio.Coerce(int), probatio.Range(min=1, max=7)
                        ),
                        probatio.Required(
                            CONF_FIRST_CHANNEL, default=DEFAULT_FIRST_CHANNEL
                        ): probatio.All(
                            probatio.Coerce(int), probatio.Range(min=1, max=60)
                        ),
                        probatio.Required(
                            CONF_LAST_CHANNEL, default=DEFAULT_LAST_CHANNEL
                        ): probatio.All(
                            probatio.Coerce(int), probatio.Range(min=1, max=60)
                        ),
                    }
                ),
                user_input or {},
            ),
            errors=errors,
        )
