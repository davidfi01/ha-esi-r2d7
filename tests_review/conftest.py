"""Review harness: fake R2D7 on a loopback TCP socket, driven through real serialx."""
import asyncio, time
import pytest





class FakeR2D7:
    """Loopback endpoint. Records every byte with a timestamp. Never moves anything."""

    def __init__(self):
        self.rx = bytearray()
        self.stamps = []
        self.writers = []
        self.live = 0
        self.server = None
        self.reply = None  # bytes to send back after each ';'

    async def start(self):
        # Reuse the same port on restart so a reconnect can find it again.
        self.server = await asyncio.start_server(self._client, "127.0.0.1", getattr(self, "port", 0))
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = f"socket://127.0.0.1:{self.port}"

    async def _client(self, reader, writer):
        self.writers.append(writer)
        self.live += 1  # clients that have not hung up yet
        try:
            while data := await reader.read(64):
                self.rx += data
                self.stamps.append((time.monotonic(), bytes(data)))
                if self.reply:
                    writer.write(self.reply * data.count(b";"))
        except ConnectionError:
            pass
        finally:
            self.live -= 1

    async def drop_clients(self):
        for w in self.writers:
            w.transport.abort()
        self.writers.clear()
        await asyncio.sleep(0.05)

    async def stop(self):
        await self.drop_clients()
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
async def r2d7(socket_enabled):
    f = FakeR2D7()
    await f.start()
    yield f
    await f.stop()
