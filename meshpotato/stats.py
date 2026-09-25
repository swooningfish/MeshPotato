"""Stats and uptime."""

import asyncio
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from meshcore import EventType

from . import config as cfg
from . import state
from . import wx
from .text import duration

_LOGGER = logging.getLogger("meshpotato_bot")


class Stats:
    """Counters since the bot started. Kept in memory, so a restart clears them."""

    def __init__(self):
        self.started = time.monotonic()
        self.started_at = datetime.now(timezone.utc)
        self.heard = 0              # messages heard on listened channels and in DMs
        self.commands: Counter = Counter()
        self.limited = 0            # commands dropped by a rate limit
        self.sent = 0
        self.send_failed = 0


counters = Stats()


def _host_uptime() -> Optional[float]:
    try:
        with open("/proc/uptime", encoding="ascii") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def format_uptime() -> str:
    since = counters.started_at.astimezone(cfg.TIMEZONE)
    text = f"Bot up {duration(time.monotonic() - counters.started)} (since {since:%a %d %b %H:%M})"
    host = _host_uptime()
    if host is not None:
        text += f" | System up {duration(host)}"
    return ("⏱️ " if cfg.USE_EMOJI else "") + text


def format_stats(battery_mv: Optional[int] = None, budget: Optional[int] = None) -> str:
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    total = sum(counters.commands.values())
    tail = [f"Heard {counters.heard}", f"Sent {counters.sent}"]
    if counters.send_failed:
        tail.append(f"Failed {counters.send_failed}")
    tail.append(f"Limited {counters.limited}")
    tail.append(f"WX API {wx.daily_calls.used_today()}/{cfg.WX_DAILY_CALL_BUDGET}")
    if battery_mv:
        tail.append(("🔋" if cfg.USE_EMOJI else "Batt ") + f"{battery_mv / 1000:.2f}V")
    head = ("📊 " if cfg.USE_EMOJI else "") + f"Cmds {total}"
    # Show the busiest commands, as many as fit
    for n in (3, 2, 1, 0):
        top = ", ".join(f"{c.lstrip('!')} {k}" for c, k in counters.commands.most_common(n))
        text = " | ".join([head + (f" ({top})" if top else "")] + tail)
        if len(text.encode("utf-8")) <= budget:
            return text
    return text


async def battery_mv() -> Optional[int]:
    if state.radio is None:
        return None
    try:
        result = await asyncio.wait_for(state.radio.commands.get_bat(), timeout=5)
        if result.type == EventType.ERROR:
            return None
        return int((result.payload or {}).get("level") or 0) or None
    except Exception as ex:
        _LOGGER.debug("Battery read failed: %s", ex)
        return None
