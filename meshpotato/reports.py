"""Reports: {tokens} in scheduled messages, --flags on the command line."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

from . import config as cfg
from .wx import get_weather
from .alerts import get_warnings
from .astro import format_moon, get_sun
from .aurora import get_aurora
from .air import get_air
from .bands import get_hf, get_uhf, get_vhf

# ---------- Reports: {tokens} in scheduled messages, --flags on the command line ----------


@dataclass
class Report:
    """A reply that a scheduled message ({name} or {name:place}) or the command line
    (--name) can ask for as well as a command."""
    fetch: Callable[..., Awaitable[Optional[str]]]     # fetch(place, budget=None, quiet=False)
    place: bool = True                                  # takes a [location]
    needs_wx: bool = False                              # needs a Met Office API key
    # used instead of fetch in scheduled messages; None cancels the whole message
    scheduled: Optional[Callable[..., Awaitable[Optional[str]]]] = None


async def _moon_report(place: str = "", budget: Optional[int] = None, quiet: bool = False) -> str:
    return format_moon()


REPORTS: dict[str, Report] = {
    "wx": Report(lambda p, budget=None, quiet=False: get_weather(p, "now", budget, quiet), needs_wx=True),
    "wxh": Report(lambda p, budget=None, quiet=False: get_weather(p, "hours", budget, quiet), needs_wx=True),
    "wxf": Report(lambda p, budget=None, quiet=False: get_weather(p, "daily", budget, quiet), needs_wx=True),
    "warn": Report(lambda p, budget=None, quiet=False: get_warnings(p, budget, quiet),
                   scheduled=lambda p: get_warnings(p, only_live=True)),
    "sun": Report(lambda p, budget=None, quiet=False: get_sun(p, quiet)),
    "moon": Report(_moon_report, place=False),
    "aurora": Report(lambda p="", budget=None, quiet=False: get_aurora(), place=False),
    "aq": Report(lambda p, budget=None, quiet=False: get_air(p, "aq", budget, quiet)),
    "pollen": Report(lambda p, budget=None, quiet=False: get_air(p, "pollen", budget, quiet)),
    "hf": Report(lambda p="", budget=None, quiet=False: get_hf(budget), place=False),
    "vhf": Report(lambda p="", budget=None, quiet=False: get_vhf(budget), place=False),
    "uhf": Report(lambda p, budget=None, quiet=False: get_uhf(p, quiet)),
}
TOKEN_RE = re.compile(r"\{(\w+)(?::([^}]*))?\}")


async def expand_tokens(text: str) -> Optional[str]:
    """Fill in {time}, {date} and the REPORTS tokens. Each distinct token is fetched once.
    None when a token says the message shouldn't go, e.g. {warn} with no warnings."""
    now = datetime.now(cfg.TIMEZONE)
    text = text.replace("{time}", now.strftime("%H:%M")).replace("{date}", now.strftime("%a %d %b"))
    values: dict[str, str] = {}
    for m in TOKEN_RE.finditer(text):
        report = REPORTS.get(m.group(1))
        if report and (report.place or m.group(2) is None) and m.group(0) not in values:
            if report.scheduled:
                value = await report.scheduled(m.group(2) or "")
                if value is None:
                    return None
            else:
                value = await report.fetch(m.group(2) or "")
            values[m.group(0)] = value or ""
    return TOKEN_RE.sub(lambda m: values.get(m.group(0), m.group(0)), text)
