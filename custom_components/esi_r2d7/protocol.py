"""ESI R2D7 v4.1 RS-232 commands over serialx, including AUX-2 proxies.

STOP is universal to the addressed R2D7 system; a channel suffix is ignored.

Confirmed on real hardware: both "*1s;" and the per-blind
form "*1sNN;" stop every moving blind. There is no per-blind stop, so only the
documented "*1s;" is sent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import logging

import serialx

_LOGGER = logging.getLogger(__name__)

# serialx.SerialException derives from Exception, not OSError, so it must be
# listed explicitly (covers InvalidSettingsError, UnknownUriScheme, ...).
CONNECT_ERRORS = (OSError, TimeoutError, ValueError, serialx.SerialException)

MIN_SYSTEM, MAX_SYSTEM = 1, 7
MIN_CHANNEL, MAX_CHANNEL = 1, 60

# Controller replies documented in the v4.1 protocol. Unverified on hardware.
_REPLY_REJECTED = ord("U")
_REPLY_OVERFLOW = ord("O")
_REPLY_XOFF = 0x13

# One user action can ask for Stop on many blinds at once. They are all the
# same universal command, so identical Stops inside this window collapse into
# one frame, provided no Open or Close was sent in between.
STOP_COALESCE_SECONDS = 0.3


def command(system: int, operation: str, channel: int | None = None) -> bytes:
    """Build an ASCII command. Channel 00 (broadcast) is intentionally excluded."""
    # type() rather than isinstance(): bool and float would format as
    # "*Trueo01;" / "*1.0o01;" and reach the wire as malformed frames.
    if type(system) is not int or not MIN_SYSTEM <= system <= MAX_SYSTEM:
        raise ValueError("System must be an integer between 1 and 7")
    if operation == "s":
        if channel is not None:
            raise ValueError("STOP is universal and takes no channel")
        return f"*{system}s;".encode("ascii")
    if operation not in ("o", "c"):
        raise ValueError("Operation must be 'o', 'c', or 's'")
    if type(channel) is not int or not MIN_CHANNEL <= channel <= MAX_CHANNEL:
        raise ValueError("Channel must be an integer between 1 and 60")
    return f"*{system}{operation}{channel:02d};".encode("ascii")


class R2D7Connection:
    """One serial connection and a lock shared among R2D7 blind entities."""

    def __init__(self, port: str, system: int) -> None:
        self.port = port
        self.system = system
        self._serial: serialx.AsyncSerial | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self.rejected_commands = 0
        self.buffer_overflows = 0
        self._last_stop_at: float | None = None
        self._moved_at: dict[int, float] = {}

    @property
    def connected(self) -> bool:
        return self._serial is not None

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call `listener` whenever the link comes up or is lost."""
        self._listeners.append(listener)

        def _remove() -> None:
            with suppress(ValueError):
                self._listeners.remove(listener)

        return _remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def connect(self) -> None:
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        if self._serial is not None:
            return
        # Home Assistant registers the esphome-hass:// URI handler in serialx.
        serial = serialx.async_serial_for_url(
            self.port, baudrate=9600, bytesize=8, parity="N", stopbits=1
        )
        await serial.open()
        self._serial = serial
        self._reader_task = asyncio.create_task(self._read_loop(serial))
        self._notify()

    async def _read_loop(self, serial: serialx.AsyncSerial) -> None:
        """Watch the link and log controller replies.

        Reading is the only way to learn that an idle link has dropped.
        """
        try:
            while data := await serial.read(64):
                self._handle_reply(data)
        except CONNECT_ERRORS as err:
            # Only the type: the message can contain the port URL and its key.
            _LOGGER.debug("R2D7 serial read failed: %s", type(err).__name__)
        if self._serial is not serial:
            return  # deliberate disconnect
        self._serial = None
        self._reader_task = None
        # The last Stop may not have reached the controller; never skip the next.
        self._last_stop_at = None
        with suppress(Exception):
            serial.abort()
        _LOGGER.warning("ESI R2D7 serial link lost")
        self._notify()

    def _handle_reply(self, data: bytes) -> None:
        _LOGGER.debug("R2D7 reply: %r", data)
        for byte in data:
            if byte == _REPLY_REJECTED:
                self.rejected_commands += 1
                _LOGGER.warning("ESI R2D7 reported a command it could not understand")
            elif byte == _REPLY_OVERFLOW:
                self.buffer_overflows += 1
                _LOGGER.error(
                    "ESI R2D7 reported a buffer overflow; "
                    "it discards all pending commands when this happens"
                )
            elif byte == _REPLY_XOFF:
                _LOGGER.warning(
                    "ESI R2D7 asked the host to pause (its buffer is half full)"
                )

    async def _close_locked(self) -> None:
        serial, self._serial = self._serial, None
        task, self._reader_task = self._reader_task, None
        self._last_stop_at = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if serial is not None:
            with suppress(*CONNECT_ERRORS):
                await serial.close()

    async def disconnect(self) -> None:
        """Close deliberately. Listeners are not told: this is not a link loss."""
        async with self._lock:
            await self._close_locked()

    async def _write_locked(self, payload: bytes) -> None:
        await self._connect_locked()
        assert self._serial is not None  # noqa: S101 - narrowed for type checkers
        await self._serial.write(payload)

    async def _send_locked(self, payload: bytes, resend_once: bool) -> None:
        """Write one command; the caller holds the lock.

        On a write failure the link is dropped. With `resend_once` the link is
        reopened and the command sent again; use it only for commands that are
        safe to repeat.
        """
        try:
            await self._write_locked(payload)
            return
        except CONNECT_ERRORS:
            await self._close_locked()
            if not resend_once:
                self._notify()
                raise
        try:
            await self._write_locked(payload)
        except CONNECT_ERRORS:
            await self._close_locked()
            self._notify()
            raise

    async def send(self, payload: bytes, *, resend_once: bool = False) -> None:
        async with self._lock:
            # Anything sent after a Stop may start movement again, so the next
            # Stop must not be treated as a duplicate.
            self._last_stop_at = None
            await self._send_locked(payload, resend_once)

    async def open_blind(self, channel: int) -> None:
        await self.send(command(self.system, "o", channel))
        self._moved_at[channel] = asyncio.get_running_loop().time()

    async def close_blind(self, channel: int) -> None:
        await self.send(command(self.system, "c", channel))
        self._moved_at[channel] = asyncio.get_running_loop().time()

    def take_recently_moved(self, window: float) -> set[int]:
        """Return channels sent Open or Close within `window` seconds, then forget them.

        Used after a Stop: those are the blinds a universal Stop may have
        caught mid-travel. Forgetting them means one Stop accounts for them once.
        """
        now = asyncio.get_running_loop().time()
        recent = {ch for ch, at in self._moved_at.items() if now - at <= window}
        self._moved_at.clear()
        return recent

    async def stop_system(self) -> None:
        """Stop ALL moving blinds belonging to this R2D7 system.

        STOP is idempotent, so it is resent once after a failed write. Open and
        Close are not resent: the protocol notes that a repeated Open or Close
        can act as a stop on some motor controls.

        The lock is first-in first-out, so a Stop requested after an Open is
        always sent after that Open and is never dropped as a duplicate.
        """
        async with self._lock:
            now = asyncio.get_running_loop().time()
            if (
                self._last_stop_at is not None
                and now - self._last_stop_at < STOP_COALESCE_SECONDS
            ):
                return
            await self._send_locked(command(self.system, "s"), True)
            self._last_stop_at = asyncio.get_running_loop().time()
