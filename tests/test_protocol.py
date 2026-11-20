"""Standalone tests for ESI R2D7 command encoding and serial lifecycle.

Run with: python esi_r2d7_integration/tests/test_protocol.py
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "esi_r2d7" / "protocol.py"
spec = importlib.util.spec_from_file_location("esi_r2d7_protocol_test", MODULE_PATH)
assert spec and spec.loader
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)


def check_encoding() -> None:
    assert protocol.command(1, "o", 1) == b"*1o01;"
    assert protocol.command(1, "c", 16) == b"*1c16;"
    assert protocol.command(7, "o", 60) == b"*7o60;"
    assert protocol.command(1, "s") == b"*1s;"
    for args in [(1, "s", 1), (0, "o", 1), (8, "c", 1), (1, "o", 0), (1, "c", 61), (1, "x", 1), (True, "o", 1), (1.0, "o", 1), (1, "o", True)]:
        try:
            protocol.command(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected ValueError for {args}")


class FakeSerial:
    """Mirrors the parts of serialx.AsyncSerial the driver uses."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self._eof = asyncio.Event()

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True
        self._eof.set()

    def abort(self) -> None:
        self.closed = True
        self._eof.set()

    async def read(self, n: int = -1) -> bytes:
        await self._eof.wait()
        return b""

    async def write(self, data: bytes) -> None:
        self.writes.append(data)


async def check_connection() -> None:
    serial = FakeSerial()
    with patch.object(protocol.serialx, "async_serial_for_url", return_value=serial) as factory:
        connection = protocol.R2D7Connection("esphome-hass://test", 1)
        await connection.connect()
        await asyncio.gather(connection.open_blind(1), connection.close_blind(16))
        await connection.stop_system()
        # gather() does not promise which of the two lands first.
        assert sorted(serial.writes[:2]) == [b"*1c16;", b"*1o01;"]
        assert serial.writes[2] == b"*1s;"
        factory.assert_called_once_with("esphome-hass://test", baudrate=9600, bytesize=8, parity="N", stopbits=1)
        await connection.disconnect()
        assert serial.closed
        assert not connection.connected


if __name__ == "__main__":
    check_encoding()
    asyncio.run(check_connection())
    print("PASS: protocol encoding, universal STOP, serial locking and lifecycle")
