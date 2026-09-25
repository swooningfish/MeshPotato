"""Air quality and pollen (Open-Meteo, CAMS Europe model, free, no key).

Terms: non-commercial use, CC BY 4.0 so replies credit Open-Meteo,
under 10,000 calls a day. https://open-meteo.com/en/terms
"""

import asyncio
import logging
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

from . import config as cfg
from . import geo
from . import net
from .geo import LocationError
from .net import TTLCache

_LOGGER = logging.getLogger("meshpotato_bot")

AIR_API = "https://air-quality-api.open-meteo.com/v1/air-quality"
AIR_CREDIT = "Open-Meteo"
AIR_POLLUTANTS = [("pm2_5", "PM2.5"), ("pm10", "PM10"), ("nitrogen_dioxide", "NO2"), ("ozone", "O3")]
# European Air Quality Index bands (European Environment Agency): upper bound -> name
EAQI_BANDS = [(20, "Good"), (40, "Fair"), (60, "Moderate"), (80, "Poor"), (100, "Very poor")]
EAQI_EMOJI = {"Good": "🟢", "Fair": "🟢", "Moderate": "🟡", "Poor": "🟠", "Very poor": "🔴",
              "Extremely poor": "🟣"}
# Open-Meteo pollen types that grow in the UK. Olive is left out
POLLEN_TYPES = [("grass_pollen", "Grass"), ("birch_pollen", "Birch"), ("alder_pollen", "Alder"),
                ("mugwort_pollen", "Mugwort"), ("ragweed_pollen", "Ragweed")]

_air_cache = TTLCache(lambda: cfg.AIR_CACHE_SEC)


def eaqi_band(value: Optional[float]) -> str:
    if value is None:
        return "Unknown"
    for upper, name in EAQI_BANDS:
        if value <= upper:
            return name
    return "Extremely poor"


def pollen_level(kind: str, grains: float) -> str:
    """'Moderate' from POLLEN_LEVELS (low-to-moderate, moderate-to-high, high-to-very-high),
    or '' for a type with no thresholds set."""
    limits = cfg.POLLEN_LEVELS.get(kind.lower())
    if not limits:
        return ""
    for limit, name in zip(limits, ("Low", "Moderate", "High")):
        if grains < limit:
            return name
    return "Very high"


def _fetch_air_sync(lat: float, lon: float) -> dict:
    key = (round(lat, 2), round(lon, 2))
    cached = _air_cache.get(key)
    if cached is not None:
        return cached
    params = {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "current": ",".join(["european_aqi"] + [k for k, _ in AIR_POLLUTANTS]),
        "hourly": ",".join(k for k, _ in POLLEN_TYPES),
        "forecast_days": 2, "timezone": str(cfg.TIMEZONE),
    }
    data = net.get_json(f"{AIR_API}?{urllib.parse.urlencode(params)}")
    _air_cache.put(key, data)
    return data


def format_air(data: dict, label: str, budget: Optional[int] = None) -> str:
    """'🟢 Norwich air: Fair (EAQI 22) | PM2.5 5 PM10 9 NO2 7 O3 63 µg/m³ | Open-Meteo'."""
    cur = data.get("current") or {}
    aqi = cur.get("european_aqi")
    band = eaqi_band(aqi)
    head = (f"{EAQI_EMOJI.get(band, '❔')} {label} air: " if cfg.USE_EMOJI else f"{label} air: ") + band
    if aqi is not None:
        head += f" (EAQI {aqi:.0f})"
    levels = " ".join(f"{name} {cur[k]:.0f}" for k, name in AIR_POLLUTANTS if cur.get(k) is not None)
    unit = "µg/m³" if cfg.USE_EMOJI else "ug/m3"
    parts = [head] + ([f"{levels} {unit}"] if levels else []) + [AIR_CREDIT]
    text = " | ".join(parts)
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    if len(text.encode("utf-8")) > budget:          # drop the pollutant detail before the index
        text = " | ".join([head, AIR_CREDIT])
    return text


def pollen_peaks(data: dict, now: Optional[datetime] = None, hours: int = 24) -> dict[str, float]:
    """Highest count of each pollen type over the next `hours`, in grains/m³."""
    now = (now or datetime.now(cfg.TIMEZONE)).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    hourly = data.get("hourly") or {}
    times = [datetime.fromisoformat(t) for t in hourly.get("time") or []]
    wanted = {i for i, t in enumerate(times) if now <= t < now + timedelta(hours=hours)}
    peaks = {}
    for key, name in POLLEN_TYPES:
        values = [v for i, v in enumerate(hourly.get(key) or []) if i in wanted and v is not None]
        if values:
            peaks[name] = max(values)
    return peaks


def format_pollen(peaks: dict[str, float], label: str, budget: Optional[int] = None) -> str:
    """'🌼 Norwich pollen 24h: Grass 45 Moderate | Birch 12 grains/m³ | Open-Meteo'.
    Types with no pollen are left out. Highest count first."""
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    head = f"🌼 {label} pollen 24h: " if cfg.USE_EMOJI else f"{label} pollen 24h: "
    unit = " grains/m³" if cfg.USE_EMOJI else " grains/m3"
    tail = f" | {AIR_CREDIT}"
    present = sorted(((n, v) for n, v in peaks.items() if round(v) > 0), key=lambda p: -p[1])
    if not present:
        return f"{head}None forecast{tail}"
    items = []
    for name, value in present:
        items.append(f"{name} {value:.0f} {pollen_level(name, value)}".strip())
        if len((head + " | ".join(items) + unit + tail).encode("utf-8")) > budget and len(items) > 1:
            items.pop()
            break
    return head + " | ".join(items) + unit + tail


async def get_air(query: str, mode: str = "aq", budget: Optional[int] = None,
                  quiet: bool = False) -> Optional[str]:
    """mode 'aq' for air quality, 'pollen' for the pollen forecast."""
    tag = "AQ" if mode == "aq" else "POLLEN"
    try:
        lat, lon, label = await asyncio.to_thread(geo._geocode_sync, query)
    except LocationError as ex:
        _LOGGER.info("Location lookup failed for %r: %s", query, ex)
        return None if quiet else f"{tag}: {ex}"
    except Exception as ex:
        _LOGGER.warning("Place lookup failed for %r: %s", query, ex)
        return f"{tag}: place lookup failed"
    try:
        data = await asyncio.to_thread(_fetch_air_sync, lat, lon)
    except Exception as ex:
        _LOGGER.warning("Open-Meteo air quality failed for %s: %s", label, ex)
        return f"{tag}: lookup failed"
    if mode == "aq":
        return format_air(data, label, budget=budget)
    return format_pollen(pollen_peaks(data), label, budget=budget)
