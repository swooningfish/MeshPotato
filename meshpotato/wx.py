"""Met Office DataHub (Site Specific) weather, with a daily call budget."""

import asyncio
import logging
import threading
import urllib.error
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import config as cfg
from . import geo
from . import net
from .geo import compass_point, LocationError
from .net import TTLCache

_LOGGER = logging.getLogger("meshpotato_bot")

WX_CODES = {
    -1: "Trace rain", 0: "Clear", 1: "Sunny", 2: "Partly cloudy", 3: "Partly cloudy",
    4: "Unknown", 5: "Mist", 6: "Fog", 7: "Cloudy", 8: "Overcast",
    9: "Lt rain shwr", 10: "Lt rain shwr", 11: "Drizzle", 12: "Light rain",
    13: "Hvy rain shwr", 14: "Hvy rain shwr", 15: "Heavy rain",
    16: "Sleet shwr", 17: "Sleet shwr", 18: "Sleet",
    19: "Hail shwr", 20: "Hail shwr", 21: "Hail",
    22: "Lt snow shwr", 23: "Lt snow shwr", 24: "Light snow",
    25: "Hvy snow shwr", 26: "Hvy snow shwr", 27: "Heavy snow",
    28: "Thunder shwr", 29: "Thunder shwr", 30: "Thunder",
}
WX_EMOJI = {
    -1: "🌦️", 0: "🌙", 1: "☀️", 2: "☁️", 3: "⛅",
    4: "❓", 5: "🌫️", 6: "🌫️", 7: "☁️", 8: "☁️",
    9: "🌧️", 10: "🌦️", 11: "🌧️", 12: "🌧️",
    13: "🌧️", 14: "🌧️", 15: "🌧️",
    16: "🌨️", 17: "🌨️", 18: "🌨️",
    19: "🧊", 20: "🧊", 21: "🧊",
    22: "❄️", 23: "❄️", 24: "❄️",
    25: "❄️", 26: "❄️", 27: "❄️",
    28: "⛈️", 29: "⛈️", 30: "⛈️",
}
MPH_PER_MS = 2.23694


class QuotaError(Exception):
    """The daily Met Office call budget is used up."""


class CallBudget:
    """Counts Met Office calls per UTC day. Thread safe, since fetches run in threads.
    The count lives in memory, so a restart starts it again at 0."""

    def __init__(self):
        self._lock = threading.Lock()
        self._day = None
        self.used = 0

    def used_today(self) -> int:
        with self._lock:
            return self.used if self._day == datetime.now(timezone.utc).date() else 0

    def take(self) -> bool:
        today = datetime.now(timezone.utc).date()
        with self._lock:
            if today != self._day:
                self._day, self.used = today, 0
            if self.used >= cfg.WX_DAILY_CALL_BUDGET:
                return False
            self.used += 1
            return True


_wx_cache = TTLCache(lambda: cfg.WX_CACHE_SEC)
daily_calls = CallBudget()
_fetch_locks: dict[Any, threading.Lock] = defaultdict(threading.Lock)
_fetch_locks_guard = threading.Lock()


def _speed_value(ms: Optional[float]) -> str:
    """Speed as a bare number in the configured unit."""
    if ms is None:
        return "?"
    return f"{ms * MPH_PER_MS:.0f}" if cfg.USE_MPH else f"{ms:.0f}"


def _speed(ms: Optional[float]) -> str:
    if ms is None:
        return "?"
    return _speed_value(ms) + ("mph" if cfg.USE_MPH else "m/s")


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def wx_available() -> bool:
    """The Met Office weather commands only run with an API key."""
    return bool(cfg.MET_OFFICE_API_KEY)


def _fetch_metoffice_sync(kind: str, lat: float, lon: float) -> dict:
    if not cfg.MET_OFFICE_API_KEY:
        raise RuntimeError("METOFFICE_API_KEY not set")
    key = (kind, round(lat, 2), round(lon, 2))
    with _fetch_locks_guard:
        lock = _fetch_locks[key]
    # One fetch per place at a time, so two quick requests share one API call
    with lock:
        cached = _wx_cache.get(key)
        if cached:
            return cached
        if not daily_calls.take():
            raise QuotaError(f"{cfg.WX_DAILY_CALL_BUDGET} calls used today")
        params = urllib.parse.urlencode({
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "excludeParameterMetadata": "true",
            "includeLocationName": "true",
        })
        data = net.get_json(f"{cfg.MET_OFFICE_BASE}{kind}?{params}", headers={"apikey": cfg.MET_OFFICE_API_KEY})
        _wx_cache.put(key, data)
        return data


def format_hourly(data: dict, label: str) -> str:
    props = data["features"][0]["properties"]
    series = props.get("timeSeries") or []
    now = datetime.now(cfg.TIMEZONE)
    entry = next((e for e in series if _parse_time(e["time"]) >= now - timedelta(minutes=30)),
                 series[-1] if series else None)
    if not entry:
        return f"WX {label}: no data"
    t = _parse_time(entry["time"]).astimezone(cfg.TIMEZONE)
    code = entry.get("significantWeatherCode")
    desc = WX_CODES.get(code, "?")
    temp = entry.get("screenTemperature")
    feels = entry.get("feelsLikeTemperature")
    rh = entry.get("screenRelativeHumidity")
    pop = entry.get("probOfPrecipitation")
    wind = f"{compass_point(entry.get('windDirectionFrom10m'))} {_speed(entry.get('windSpeed10m'))}"
    gust = _speed_value(entry.get("windGustSpeed10m"))
    if cfg.USE_EMOJI:
        parts = [
            f"{WX_EMOJI.get(code, '')} {label} {t:%H:%M}",
            desc,
            (f"\U0001F321️{temp:.0f}°C" + (f" (feels {feels:.0f}°)" if feels is not None else ""))
            if temp is not None else "",
            f"\U0001F4A8{wind} gust {gust}",
            f"☔{pop}%" if pop is not None else "",
            f"\U0001F4A7humidity {rh:.0f}%" if rh is not None else "",
        ]
        return " ".join(p for p in parts if p)
    parts = [
        f"{label} {t:%H:%M}",
        desc,
        f"{temp:.0f}C feels {feels:.0f}C" if temp is not None and feels is not None
        else (f"{temp:.0f}C" if temp is not None else "?C"),
        f"Wind {wind} gust {gust}",
        f"Rain {pop}%" if pop is not None else "",
        f"Humidity {rh:.0f}%" if rh is not None else "",
    ]
    return " | ".join(p for p in parts if p)


def format_daily(data: dict, label: str, days: int = 3) -> str:
    props = data["features"][0]["properties"]
    today = datetime.now(cfg.TIMEZONE).date()
    out = []
    for e in props.get("timeSeries") or []:
        d = _parse_time(e["time"]).astimezone(cfg.TIMEZONE).date()
        if d < today:
            continue
        code = e.get("daySignificantWeatherCode")
        desc = WX_CODES.get(code, "?")
        hi = e.get("dayMaxScreenTemperature")
        lo = e.get("nightMinScreenTemperature")
        pop = e.get("dayProbabilityOfPrecipitation")
        hi_s = f"{hi:.0f}" if hi is not None else "?"
        lo_s = f"{lo:.0f}" if lo is not None else "?"
        pop_s = pop if pop is not None else "?"
        if cfg.USE_EMOJI:
            out.append(f"{d:%a} {WX_EMOJI.get(code, desc)} {hi_s}/{lo_s}° ☔{pop_s}%")
        else:
            out.append(f"{d:%a} {desc} {hi_s}/{lo_s}C {pop_s}%")
        if len(out) >= days:
            break
    if not out:
        return f"{label}: no forecast"
    if cfg.USE_EMOJI:
        return f"\U0001F4C5 {label}: " + " | ".join(out)
    return f"{label}: " + " | ".join(out)


def format_hours(data: dict, label: str, hours: Optional[int] = None, step: Optional[int] = None,
                 budget: Optional[int] = None) -> str:
    """Hour-by-hour outlook. Adds entries until `hours` or the byte budget is reached."""
    hours = cfg.WXH_HOURS if hours is None else hours
    step = cfg.WXH_STEP_HOURS if step is None else step
    if budget is None:
        budget = cfg.MAX_REPLY_BYTES - cfg.WXH_MENTION_RESERVE
    props = data["features"][0]["properties"]
    series = props.get("timeSeries") or []
    now = datetime.now(cfg.TIMEZONE)
    upcoming = [e for e in series if _parse_time(e["time"]) >= now - timedelta(minutes=30)]
    upcoming = upcoming[:: max(1, step)]
    head = f"\U0001F552 {label}: " if cfg.USE_EMOJI else f"{label}: "
    sep = " | "
    text = head
    count = 0
    for e in upcoming:
        if count >= hours:
            break
        t = _parse_time(e["time"]).astimezone(cfg.TIMEZONE)
        code = e.get("significantWeatherCode")
        temp = e.get("screenTemperature")
        pop = e.get("probOfPrecipitation")
        temp_s = f"{temp:.0f}" if temp is not None else "?"
        pop_s = pop if pop is not None else "?"
        if cfg.USE_EMOJI:
            item = f"{t:%H}h {WX_EMOJI.get(code, '?')}{temp_s}° ☔{pop_s}%"
        else:
            item = f"{t:%H}h {WX_CODES.get(code, '?')} {temp_s}C {pop_s}%"
        candidate = text + (sep if count else "") + item
        if len(candidate.encode("utf-8")) > budget:
            break
        text = candidate
        count += 1
    if not count:
        return f"{label}: no hourly data"
    return text


async def get_weather(query: str, mode: str = "now", budget: Optional[int] = None,
                      quiet: bool = False) -> Optional[str]:
    """mode: "now", "hours" or "daily".
    quiet=True returns None instead of an error reply when the place isn't found.
    Returns None with no Met Office API key, so commands and {wx} tokens are skipped."""
    if not wx_available():
        _LOGGER.debug("No Met Office API key, weather lookup for %r skipped", query)
        return None
    try:
        lat, lon, label = await asyncio.to_thread(geo._geocode_sync, query)
    except LocationError as ex:
        _LOGGER.info("Location lookup failed for %r: %s", query, ex)
        return None if quiet else f"WX: {ex}"
    except urllib.error.HTTPError as ex:
        _LOGGER.warning("postcodes.io HTTP %s for %r: %s", ex.code, query, ex.reason)
        return "WX: place lookup error"
    except Exception as ex:
        _LOGGER.warning("Place lookup failed for %r: %s", query, ex)
        return "WX: place lookup failed"

    try:
        kind = "daily" if mode == "daily" else "hourly"
        data = await asyncio.to_thread(_fetch_metoffice_sync, kind, lat, lon)
        if mode == "daily":
            return format_daily(data, label)
        if mode == "hours":
            return format_hours(data, label, budget=budget)
        return format_hourly(data, label)
    except QuotaError as ex:
        _LOGGER.warning("Met Office daily budget reached: %s", ex)
        return "WX: daily quota used, try tomorrow"
    except urllib.error.HTTPError as ex:
        _LOGGER.warning("Met Office HTTP %s: %s", ex.code, ex.reason)
        if ex.code in (401, 403):
            return "WX: API key rejected"
        if ex.code == 429:
            return "WX: API quota hit, try later"
        return f"WX: service error {ex.code}"
    except Exception as ex:
        _LOGGER.warning("Met Office lookup failed: %s", ex)
        return "WX: lookup failed"
