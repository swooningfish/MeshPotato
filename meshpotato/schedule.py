"""Scheduled messages."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from . import config as cfg
from .state import mute_remaining
from .tx import Sender
from .reports import expand_tokens

_LOGGER = logging.getLogger("meshpotato_bot")

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _hm(s: str) -> tuple[int, int]:
    h, m = s.strip().split(":")
    return int(h), int(m)


def _at_is_yearly(at: str) -> bool:
    """"MM-DD HH:MM" (no year) repeats every year."""
    return at.strip().split()[0].count("-") == 1


def _parse_at(at: str, year: int) -> datetime:
    at = at.strip()
    return datetime.strptime(f"{year}-{at}" if _at_is_yearly(at) else at, "%Y-%m-%d %H:%M")


def due_slot(entry: dict, now: datetime) -> Optional[str]:
    """Return a unique slot id if the entry is due now, else None."""
    grace = timedelta(seconds=cfg.SCHEDULE_GRACE_SEC)

    if "at" in entry:
        try:
            target = _parse_at(entry["at"], now.year).replace(tzinfo=cfg.TIMEZONE)
        except ValueError:      # "02-29" outside a leap year
            return None
        return f"at:{target:%Y-%m-%d %H:%M}" if target <= now < target + grace else None

    if "time" in entry:
        days = [d.lower()[:3] for d in entry.get("days", DAY_NAMES)]
        if DAY_NAMES[now.weekday()] not in days:
            return None
        h, m = _hm(entry["time"])
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return f"time:{target:%Y-%m-%d %H:%M}" if target <= now < target + grace else None

    if "every_minutes" in entry:
        step = int(entry["every_minutes"]) * 60
        h, m = _hm(entry.get("start", "00:00"))
        anchor = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if anchor > now:
            anchor -= timedelta(days=1)
        elapsed = (now - anchor).total_seconds()
        slot_start = anchor + timedelta(seconds=(elapsed // step) * step)
        return f"every:{slot_start:%Y-%m-%d %H:%M}" if now - slot_start < grace else None

    return None


def validate_schedule(entries: list[dict]) -> list[dict]:
    valid = []
    for i, e in enumerate(entries):
        name = e.get("name", f"#{i}")
        try:
            if "text" not in e or ("channel" not in e and "dm" not in e):
                raise ValueError("needs 'text' and 'channel' or 'dm'")
            if "at" in e:
                _parse_at(e["at"], 2000)     # leap year, so "02-29" is accepted
            elif "time" in e:
                h, m = _hm(e["time"])
                if not (0 <= h < 24 and 0 <= m < 60):
                    raise ValueError(f"bad time {e['time']}")
                bad = [d for d in e.get("days", []) if d.lower()[:3] not in DAY_NAMES]
                if bad:
                    raise ValueError(f"bad days {bad}")
            elif "every_minutes" in e:
                if int(e["every_minutes"]) < 5:
                    raise ValueError("every_minutes must be >= 5")
                _hm(e.get("start", "00:00"))
            else:
                raise ValueError("needs 'time', 'at' or 'every_minutes'")
            valid.append(e)
        except Exception as ex:
            _LOGGER.error("Schedule entry %s ignored: %s", name, ex)
    return valid


async def scheduler(sender: Sender, entries: list[dict]) -> None:
    fired: dict[int, str] = {}
    _LOGGER.info("Scheduler running with %d entries", len(entries))
    while True:
        now = datetime.now(cfg.TIMEZONE)
        for i, entry in enumerate(entries):
            slot = due_slot(entry, now)
            if not slot or fired.get(i) == slot:
                continue
            fired[i] = slot
            if mute_remaining() > 0:
                _LOGGER.info("Schedule '%s' skipped, bot is muted (%s)", entry.get("name", i), slot)
                continue
            _LOGGER.info("Schedule '%s' firing (%s)", entry.get("name", i), slot)
            text = await expand_tokens(entry["text"])
            if not text.strip():                # e.g. "{wx}" alone with no Met Office API key
                _LOGGER.info("Schedule '%s' skipped, nothing to send", entry.get("name", i))
                continue
            if "channel" in entry:
                sender.channel(int(entry["channel"]), text)
            else:
                sender.dm(entry["dm"], text)
        await asyncio.sleep(cfg.SCHEDULE_TICK_SEC)
