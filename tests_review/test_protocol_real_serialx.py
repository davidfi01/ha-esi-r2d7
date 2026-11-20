"""protocol.py against REAL serialx (socket:// loopback). No hardware."""
import asyncio, pytest, serialx
from custom_components.esi_r2d7 import protocol
from custom_components.esi_r2d7.protocol import R2D7Connection, command

CAUGHT = protocol.CONNECT_ERRORS  # what __init__/config_flow/cover catch


@pytest.mark.parametrize("args,expected", [
    ((1, "o", 1), b"*1o01;"), ((7, "c", 60), b"*7c60;"), ((1, "o", 9), b"*1o09;"),
    ((1, "o", 10), b"*1o10;"), ((1, "c", 16), b"*1c16;"), ((1, "s"), b"*1s;"),
])
def test_valid_encoding(args, expected):
    assert command(*args) == expected


@pytest.mark.parametrize("args", [
    (0, "o", 1), (8, "o", 1), (1, "o", 0), (1, "o", 61), (1, "s", 1), (1, "x", 1),
    (1, "O", 1), (1, "o", None), (1, "o;*1c", 1), (1, "о", 1), (-1, "o", 1),
])
def test_invalid_rejected_with_valueerror(args):
    with pytest.raises(ValueError):
        command(*args)


@pytest.mark.parametrize("args", [(True, "o", 1), (1.0, "o", 1), (1, "o", 1.0), ("1", "o", 1), (None, "o", 1), (1, "o", "1"), (1, "o", True)])
def test_wrong_types_never_produce_bytes(args):
    """A malformed frame must never reach the wire; any exception type is acceptable."""
    try:
        out = command(*args)
    except (ValueError, TypeError):
        return
    pytest.fail(f"command{args} produced {out!r}")


async def test_roundtrip_bytes(r2d7):
    c = R2D7Connection(r2d7.url, 1)
    await c.connect()
    await c.open_blind(1); await c.close_blind(16); await c.stop_system()
    await asyncio.sleep(0.05)
    assert bytes(r2d7.rx) == b"*1o01;*1c16;*1s;"
    await c.disconnect()
    await c.disconnect()  # double close is safe


async def test_unknown_scheme_exception_is_caught_by_callers():
    c = R2D7Connection("bogus-scheme://x", 1)
    with pytest.raises(CAUGHT):
        await c.connect()


async def test_serialexception_is_covered():
    assert not issubclass(serialx.SerialException, OSError)  # why it must be listed
    assert issubclass(serialx.SerialException, CAUGHT)


@pytest.mark.xfail(strict=True, reason="REVIEW H3: pacing queue on hold pending hardware facts")
async def test_burst_is_paced_for_r2d7_buffer(r2d7):
    """Spec fn 11/14: 256-byte buffer, 0.75 s per buffered command, XOFF at half full.
    60 simultaneous commands = 360 bytes. Driver must pace or honour XOFF."""
    c = R2D7Connection(r2d7.url, 1)
    await c.connect()
    await asyncio.gather(*(c.open_blind(ch) for ch in range(1, 61)))
    await asyncio.sleep(0.2)
    span = r2d7.stamps[-1][0] - r2d7.stamps[0][0]
    await c.disconnect()
    assert len(r2d7.rx) == 360
    assert span > 1.0, f"360 bytes delivered in {span*1000:.1f} ms with no pacing (R2D7 buffer is 256 bytes)"


async def test_idle_link_loss_is_noticed_without_sending(r2d7):
    c = R2D7Connection(r2d7.url, 1)
    seen = []
    c.add_listener(lambda: seen.append(c.connected))
    await c.connect()
    await r2d7.drop_clients()
    await asyncio.sleep(0.1)
    assert seen == [True, False] and not c.connected


async def test_stop_is_resent_after_a_failed_write(r2d7):
    """Race: link already dead but the reader has not noticed yet. STOP must still arrive."""
    c = R2D7Connection(r2d7.url, 1)
    await c.connect()

    async def dead(_):
        raise OSError(5, "link gone")
    c._serial.write = dead
    await c.stop_system()
    await asyncio.sleep(0.05)
    assert bytes(r2d7.rx) == b"*1s;" and c.connected
    await c.disconnect()


async def test_open_is_not_resent_after_a_failed_write(r2d7):
    """A repeated Open can act as a stop on some motor controls (spec fn 15)."""
    c = R2D7Connection(r2d7.url, 1)
    seen = []
    c.add_listener(lambda: seen.append(c.connected))
    await c.connect()

    async def dead(_):
        raise OSError(5, "link gone")
    c._serial.write = dead
    with pytest.raises(OSError):
        await c.open_blind(1)
    await asyncio.sleep(0.05)
    assert bytes(r2d7.rx) == b"" and not c.connected and seen == [True, False]


async def test_stop_fails_cleanly_when_controller_unreachable(r2d7):
    c = R2D7Connection(r2d7.url, 1)
    await c.connect()
    await r2d7.stop()
    await asyncio.sleep(0.1)
    with pytest.raises(CAUGHT):
        await c.stop_system()
    assert not c.connected
    await r2d7.start()  # so the fixture teardown has something to stop


async def test_error_replies_are_logged_and_counted(r2d7, caplog):
    """Spec: controller answers 'U' for a command it cannot understand, 'O' on overflow."""
    c = R2D7Connection(r2d7.url, 1)
    await c.connect()
    r2d7.reply = b"U"
    await c.open_blind(1); await asyncio.sleep(0.05)
    r2d7.reply = b"O\x13"
    await c.open_blind(2); await asyncio.sleep(0.05)
    await c.disconnect()
    assert (c.rejected_commands, c.buffer_overflows) == (1, 1)
    assert "could not understand" in caplog.text and "buffer overflow" in caplog.text and "pause" in caplog.text


async def test_cancelled_send_releases_lock(r2d7):
    c = R2D7Connection(r2d7.url, 1)
    await c.connect()
    t = asyncio.ensure_future(c.open_blind(1)); await asyncio.sleep(0); t.cancel()
    await asyncio.wait_for(c.stop_system(), 1)
    await c.disconnect()


async def test_aexit_unexpected_error_does_not_escape_disconnect():
    class Boom:
        async def open(self): ...
        async def close(self): raise serialx.SerialException("close failed")
        async def read(self, n=-1): await asyncio.Event().wait()
        async def write(self, d): ...
    from unittest.mock import patch
    with patch.object(protocol.serialx, "async_serial_for_url", return_value=Boom()):
        c = R2D7Connection("x", 1); await c.connect()
        await c.disconnect()


def test_no_per_channel_stop_exists():
    """Hardware-confirmed 2026-11-19: Stop is universal in both forms, so only '*1s;' is built."""
    assert not hasattr(protocol, "channel_stop_command")
    assert not hasattr(R2D7Connection, "stop_channel")
    with pytest.raises(ValueError):
        command(1, "s", 4)


async def test_simultaneous_stops_collapse_into_one_frame(r2d7):
    c = R2D7Connection(r2d7.url, 1)
    await c.connect()
    await asyncio.gather(*(c.stop_system() for _ in range(16)))
    await c.stop_system()
    await asyncio.sleep(0.05)
    await c.disconnect()
    assert bytes(r2d7.rx) == b"*1s;"


async def test_stop_after_a_move_is_never_dropped(r2d7):
    """Stop, Open, Stop inside the window: the second Stop must reach the controller."""
    c = R2D7Connection(r2d7.url, 1)
    await c.connect()
    await asyncio.gather(c.stop_system(), c.open_blind(4), c.stop_system(), c.close_blind(5), c.stop_system(), c.stop_system())
    await asyncio.sleep(0.05)
    await c.disconnect()
    assert bytes(r2d7.rx) == b"*1s;*1o04;*1s;*1c05;*1s;"


async def test_stops_outside_the_window_are_each_sent(r2d7):
    c = R2D7Connection(r2d7.url, 1)
    await c.connect()
    await c.stop_system()
    await asyncio.sleep(protocol.STOP_COALESCE_SECONDS + 0.1)
    await c.stop_system()
    await asyncio.sleep(0.05)
    await c.disconnect()
    assert bytes(r2d7.rx) == b"*1s;*1s;"


async def test_failed_stop_is_not_remembered_as_sent(r2d7):
    """If a Stop could not be sent, the next Stop must try again, not be skipped."""
    c = R2D7Connection(r2d7.url, 1)
    await c.connect()
    await r2d7.stop()
    await asyncio.sleep(0.1)
    with pytest.raises(CAUGHT):
        await c.stop_system()
    await r2d7.start()
    await c.stop_system()
    await asyncio.sleep(0.05)
    await c.disconnect()
    assert bytes(r2d7.rx) == b"*1s;"
