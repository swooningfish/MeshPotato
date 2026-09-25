"""Weather warnings (Met Office RSS, free, no key, not counted in the call budget) and the watcher."""

import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from . import config as cfg
from . import geo
from . import net
from .state import mute_remaining
from .geo import LocationError
from .net import TTLCache

if TYPE_CHECKING:
    from .tx import Sender

_LOGGER = logging.getLogger("meshpotato_bot")

WARN_FEED = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/"
WARN_REGIONS = {
    "uk": "UK", "os": "Orkney & Shetland", "he": "Highlands & Eilean Siar", "gr": "Grampian",
    "st": "Strathclyde", "ta": "Central, Tayside & Fife", "dg": "SW Scotland, Lothian & Borders",
    "ni": "Northern Ireland", "wl": "Wales", "nw": "North West England", "ne": "North East England",
    "yh": "Yorkshire & Humber", "wm": "West Midlands", "em": "East Midlands",
    "ee": "East of England", "sw": "South West England", "se": "London & South East England",
}
# postcodes.io region (England) or country -> Met Office warning region
_WARN_BY_REGION = {
    "north east": "ne", "north west": "nw", "yorkshire and the humber": "yh",
    "east midlands": "em", "west midlands": "wm", "east of england": "ee",
    "london": "se", "south east": "se", "south west": "sw",
    "wales": "wl", "northern ireland": "ni",
}
# Scottish council (postcodes.io admin_district) -> Met Office warning region
_WARN_BY_SCOTTISH_COUNCIL = {
    "orkney islands": "os", "shetland islands": "os",
    "highland": "he", "na h-eileanan siar": "he",
    "aberdeen city": "gr", "aberdeenshire": "gr", "moray": "gr",
    "angus": "ta", "dundee city": "ta", "perth and kinross": "ta", "fife": "ta",
    "clackmannanshire": "ta", "falkirk": "ta", "stirling": "ta",
    "argyll and bute": "st", "east ayrshire": "st", "north ayrshire": "st", "south ayrshire": "st",
    "east dunbartonshire": "st", "west dunbartonshire": "st", "east renfrewshire": "st",
    "renfrewshire": "st", "inverclyde": "st", "glasgow city": "st",
    "north lanarkshire": "st", "south lanarkshire": "st",
    "dumfries and galloway": "dg", "scottish borders": "dg", "city of edinburgh": "dg",
    "east lothian": "dg", "midlothian": "dg", "west lothian": "dg",
}
WARN_LEVELS = {"red": 0, "amber": 1, "yellow": 2}
WARN_LEVEL_EMOJI = {"red": "🔴", "amber": "🟠", "yellow": "🟡"}
WARN_HAZARD_EMOJI = [("thunder", "⛈️"), ("lightning", "⚡"), ("rain", "🌧️"), ("snow", "❄️"),
                     ("ice", "🧊"), ("wind", "💨"), ("fog", "🌫️"), ("heat", "🌡️")]
WARN_TITLE_RE = re.compile(r"^(yellow|amber|red)\s+warning\s+of\s+(.+?)(?:\s+affecting\s+(.+))?$", re.I)
WARN_VALID_RE = re.compile(r"valid from (\d{2})(\d{2}) \w{3} (\d{1,2}) (\w{3}) to (\d{2})(\d{2}) \w{3} (\d{1,2}) (\w{3})",
                           re.I)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

_warn_cache = TTLCache(lambda: cfg.WARN_CACHE_SEC)


def _warn_time(hh: str, mm: str, day: str, mon: str, now: datetime) -> Optional[datetime]:
    """The feed gives '0600 Sat 04 Nov' with no year. Pick the year that lands closest to now."""
    try:
        month = _MONTHS.index(mon.lower()[:3]) + 1
        options = [datetime(y, month, int(day), int(hh), int(mm), tzinfo=cfg.TIMEZONE)
                   for y in (now.year - 1, now.year, now.year + 1)]
    except ValueError:
        return None
    return min(options, key=lambda t: abs(t - now))


def parse_warning(title: str, description: str, now: Optional[datetime] = None) -> Optional[dict]:
    m = WARN_TITLE_RE.match(" ".join(title.split()))
    if not m:
        return None
    now = now or datetime.now(cfg.TIMEZONE)
    w = {"level": m.group(1).lower(), "hazard": m.group(2).strip(),
         "area": (m.group(3) or "").strip(), "start": None, "end": None}
    v = WARN_VALID_RE.search(description or "")
    if v:
        w["start"] = _warn_time(*v.group(1, 2, 3, 4), now)
        w["end"] = _warn_time(*v.group(5, 6, 7, 8), now)
    return w


def parse_warnings_feed(xml_bytes: bytes, now: Optional[datetime] = None) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        w = parse_warning(item.findtext("title") or "", item.findtext("description") or "", now)
        if w:
            out.append(w)
    return out


def _warn_region_sync(query: str) -> str:
    """Met Office region code for a region code, a place or DEFAULT_LOCATION."""
    q = query.strip().lower()
    if q in WARN_REGIONS and q not in {k.lower() for k in cfg.LOCATIONS}:
        return q
    lat, lon, _ = geo._geocode_sync(query)
    key = ("warn-region", round(lat, 3), round(lon, 3))
    cached = geo.geocode_cache.get(key)
    if cached:
        return cached
    data = net.get_json(f"https://api.postcodes.io/postcodes?lon={lon:.5f}&lat={lat:.5f}&limit=1&radius=2000")
    res = data.get("result") or []
    if not res:
        raise LocationError(f"no UK postcode near {lat:.2f},{lon:.2f}")
    r = res[0]
    country = (r.get("country") or "").lower()
    if country == "scotland":
        code = _WARN_BY_SCOTTISH_COUNCIL.get((r.get("admin_district") or "").lower())
    elif country == "england":
        code = _WARN_BY_REGION.get((r.get("region") or "").lower())
    else:
        code = _WARN_BY_REGION.get(country)
    code = code or "uk"
    geo.geocode_cache.put(key, code)
    return code


def _fetch_warnings_sync(code: str) -> list[dict]:
    cached = _warn_cache.get(code)
    if cached is not None:
        return cached
    body = net.get(WARN_FEED + code, accept="application/rss+xml, application/xml")
    warnings = parse_warnings_feed(body)
    _warn_cache.put(code, warnings)
    return warnings


def _warn_span(start: Optional[datetime], end: Optional[datetime], now: datetime) -> str:
    if not end:
        return ""
    if not start or start <= now:
        return f"to {end:%a %H:%M}"
    if start.date() == end.date():
        return f"{start:%a %H:%M}-{end:%H:%M}"
    return f"{start:%a %H:%M}-{end:%a %H:%M}"


def format_warnings(warnings: list[dict], code: str, budget: Optional[int] = None,
                    now: Optional[datetime] = None) -> str:
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    now = now or datetime.now(cfg.TIMEZONE)
    region = WARN_REGIONS.get(code, code)
    live = [w for w in warnings if not w["end"] or w["end"] > now]
    if not live:
        return f"✅ No weather warnings for {region}" if cfg.USE_EMOJI else f"No weather warnings for {region}"
    live.sort(key=lambda w: (WARN_LEVELS.get(w["level"], 9), w["start"] or now))
    items = []
    for w in live:
        hazard = w["hazard"].capitalize()
        span = _warn_span(w["start"], w["end"], now)
        area = f" ({w['area']})" if code == "uk" and w["area"] else ""
        if cfg.USE_EMOJI:
            icons = "".join(e for word, e in WARN_HAZARD_EMOJI if word in w["hazard"].lower())
            items.append(f"{WARN_LEVEL_EMOJI.get(w['level'], '⚠️')}{icons} {hazard} {span}{area}".strip())
        else:
            items.append(f"{w['level'].capitalize()} {hazard.lower()} {span}{area}".strip())
    text = (f"⚠️ {region}: " if cfg.USE_EMOJI else f"Warnings {region}: ")
    shown = 0
    for i, item in enumerate(items):
        rest = len(items) - i - 1
        more = f" +{rest} more" if rest else ""
        candidate = text + (" | " if shown else "") + item
        if len((candidate + more).encode("utf-8")) > budget:
            break
        text = candidate
        shown += 1
    if not shown:                   # one long warning: send it and let trim() cut it
        return text + items[0]
    if shown < len(items):
        text += f" +{len(items) - shown} more"
    return text


async def lookup_warnings(query: str, budget: Optional[int] = None,
                          quiet: bool = False) -> tuple[Optional[str], Optional[list[dict]], Optional[str]]:
    """Returns (region code, warnings, reply). Warnings is None when the lookup failed."""
    try:
        code = await asyncio.to_thread(_warn_region_sync, query)
    except LocationError as ex:
        _LOGGER.info("Warning region lookup failed for %r: %s", query, ex)
        return None, None, None if quiet else f"WARN: {ex}"
    except Exception as ex:
        _LOGGER.warning("Warning region lookup failed for %r: %s", query, ex)
        return None, None, "WARN: place lookup failed"
    try:
        warnings = await asyncio.to_thread(_fetch_warnings_sync, code)
    except Exception as ex:
        _LOGGER.warning("Met Office warnings feed failed for %s: %s", code, ex)
        return code, None, "WARN: lookup failed"
    return code, warnings, format_warnings(warnings, code, budget=budget)


async def get_warnings(query: str, budget: Optional[int] = None, quiet: bool = False) -> Optional[str]:
    return (await lookup_warnings(query, budget=budget, quiet=quiet))[2]


# ---------- Warning alerts ----------
def _warn_key(w: dict) -> tuple:
    return w["level"], w["hazard"].lower(), w["area"], w["start"], w["end"]


def _live_warn_keys(warnings: list[dict], now: datetime) -> set[tuple]:
    return {_warn_key(w) for w in warnings if not w["end"] or w["end"] > now}


class WarnWatcher:
    """After a !warn, keeps checking that region and posts to the same channel or DM when its
    warnings change: a new warning, a new level or new times, or one cancelled early. A warning
    that simply runs out isn't posted. Each !warn restarts the WARN_WATCH_HOURS clock.
    Kept in memory, so a restart ends every watch."""

    def __init__(self):
        # (kind, target, region code) -> {"until": monotonic end, "seen": warning keys last posted}
        self.watches: dict[tuple, dict] = {}

    def add(self, kind: str, target: Any, code: str, warnings: list[dict],
            now: Optional[datetime] = None) -> None:
        now = now or datetime.now(cfg.TIMEZONE)
        key = (kind, target, code)
        if key not in self.watches and len(self.watches) >= cfg.WARN_WATCH_MAX:
            _LOGGER.info("Warning watch for %s on %s %s not started, %d already running",
                         code, kind, target, cfg.WARN_WATCH_MAX)
            return
        self.watches[key] = {"until": time.monotonic() + cfg.WARN_WATCH_HOURS * 3600,
                             "seen": _live_warn_keys(warnings, now)}
        _LOGGER.info("Watching warnings for %s on %s %s for %sh", code, kind, target, cfg.WARN_WATCH_HOURS)

    async def check(self, sender: "Sender", now: Optional[datetime] = None) -> None:
        now = now or datetime.now(cfg.TIMEZONE)
        for key, watch in list(self.watches.items()):
            kind, target, code = key
            if time.monotonic() >= watch["until"]:
                del self.watches[key]
                _LOGGER.info("Warning watch for %s on %s %s ended", code, kind, target)
                continue
            try:
                warnings = await asyncio.to_thread(_fetch_warnings_sync, code)
            except Exception as ex:
                _LOGGER.warning("Met Office warnings feed failed for %s: %s", code, ex)
                continue
            current = _live_warn_keys(warnings, now)
            still_live = {k for k in watch["seen"] if not k[4] or k[4] > now}
            if current == still_live:
                watch["seen"] = current         # forget warnings that ran out, without a post
                continue
            if mute_remaining() > 0:            # post the change once the mute ends
                continue
            watch["seen"] = current
            prefix = "🔔 " if cfg.USE_EMOJI else "Update: "
            text = prefix + format_warnings(warnings, code, now=now,
                                            budget=cfg.MAX_REPLY_BYTES - len(prefix.encode("utf-8")))
            _LOGGER.info("Warnings changed for %s, posting to %s %s", code, kind, target)
            if kind == "chan":
                sender.channel(target, text)
            else:
                sender.dm(target, text)

    async def run(self, sender: "Sender") -> None:
        while True:
            await asyncio.sleep(cfg.WARN_WATCH_TICK_SEC)
            try:
                await self.check(sender)
            except Exception:
                _LOGGER.exception("Warning watch check failed")


watcher = WarnWatcher()
