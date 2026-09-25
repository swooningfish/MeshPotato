"""Transmit queue."""

import asyncio
import logging
import time

from meshcore import MeshCore, EventType

from . import config as cfg
from . import stats
from .text import trim

_LOGGER = logging.getLogger("meshpotato_bot")


class Sender:
    """Serialises radio sends and enforces MIN_TX_GAP_SEC between them."""

    def __init__(self, meshcore: MeshCore):
        self.mc = meshcore
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._last_tx = 0.0

    def channel(self, idx: int, text: str) -> None:
        self._put(("chan", idx, trim(text)))

    def dm(self, pubkey_prefix: str, text: str) -> None:
        self._put(("dm", pubkey_prefix, trim(text)))

    def room(self) -> int:
        """Free slots in the send queue."""
        return self.queue.maxsize - self.queue.qsize()

    def _put(self, item) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            _LOGGER.warning("TX queue full, dropping: %s", item)

    async def run(self) -> None:
        while True:
            kind, target, text = await self.queue.get()
            wait = cfg.MIN_TX_GAP_SEC - (time.monotonic() - self._last_tx)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                if kind == "chan":
                    result = await self.mc.commands.send_chan_msg(target, text)
                else:
                    result = await self.mc.commands.send_msg(target, text)
                if result.type == EventType.ERROR:
                    stats.counters.send_failed += 1
                    _LOGGER.error("Send to %s %s failed: %s", kind, target, result.payload)
                else:
                    stats.counters.sent += 1
                    _LOGGER.info("TX %s %s: %s", kind, target, text)
            except Exception as ex:
                stats.counters.send_failed += 1
                _LOGGER.error("Send exception: %s", ex)
            self._last_tx = time.monotonic()
            self.queue.task_done()
