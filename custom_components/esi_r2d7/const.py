"""Constants for the ESI R2D7 serial integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from esi_r2d7 import R2D7Connection

DOMAIN = "esi_r2d7"
LOGGER = logging.getLogger(__package__)
CONF_SYSTEM = "system"
CONF_FIRST_CHANNEL = "first_channel"
CONF_LAST_CHANNEL = "last_channel"
DEFAULT_SYSTEM = 1
DEFAULT_FIRST_CHANNEL = 1
DEFAULT_LAST_CHANNEL = 16
BAUDRATE = 9600

# The controller reports nothing back, so the state shown is only the last
# command sent. A universal Stop marks as "stopped" the blind it was pressed
# on plus every blind sent Open or Close within this many seconds.
RECENT_MOVE_SECONDS = 120

type R2D7ConfigEntry = ConfigEntry[R2D7Connection]
