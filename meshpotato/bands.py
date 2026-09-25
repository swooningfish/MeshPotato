"""Radio conditions: HF and VHF from N0NBH, UHF tropo from Open-Meteo.

HF and VHF: N0NBH solar feed (hamqsl.com). Free, credit N0NBH, fetch no more than
once an hour (the flux updates hourly, the rest every 3 hours). https://www.hamqsl.com/solar.html
UHF: tropospheric refraction worked out from the Open-Meteo forecast (CC BY 4.0).
"""

import asyncio
import logging
import math
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config as cfg
from . import geo
from . import net
from .geo import LocationError
from .net import TTLCache
from .astro import sun_times
from .air import AIR_CREDIT

_LOGGER = logging.getLogger("meshpotato_bot")

HAMQSL_URL = "https://www.hamqsl.com/solarxml.php"
HAMQSL_CREDIT = "N0NBH"
HAMQSL_MIN_CACHE_SEC = 3600     # N0NBH asks for hourly updates at most
HF_BANDS = [("80m-40m", "80-40m"), ("30m-20m", "30-20m"), ("17m-15m", "17-15m"), ("12m-10m", "12-10m")]
# (feed name, feed location) -> label
VHF_PHENOMENA = [(("E-Skip", "europe_6m"), "6m Es"), (("E-Skip", "europe_4m"), "4m Es"),
                 (("E-Skip", "europe"), "2m Es"), (("vhf-aurora", "northern_hemi"), "Aurora")]
TROPO_API = "https://api.open-meteo.com/v1/forecast"
TROPO_FIELDS = ["temperature_2m", "relative_humidity_2m", "surface_pressure",
                "temperature_925hPa", "relative_humidity_925hPa", "geopotential_height_925hPa"]
# Refractivity gradient (N-units per km) -> level, most refraction last. The ITU-R P.453
# limits are -79 (super-refraction) and -157 (ducting). -60 is the bot's own early hint,
# because the surface-to-925 hPa layer (about 700 m) smooths out thin inversions.
TROPO_LEVELS = [(0, "Below normal"), (-60, "Normal"), (-79, "Slightly enhanced"),
                (-157, "Enhanced"), (float("-inf"), "Ducting likely")]
BAND_EMOJI = {"good": "🟢", "fair": "🟡", "poor": "🔴"}
TROPO_EMOJI = {"Below normal": "🔽", "Normal": "➖", "Slightly enhanced": "🔼", "Enhanced": "⏫",
               "Ducting likely": "🚀"}

_hamqsl_cache = TTLCache(lambda: max(HAMQSL_MIN_CACHE_SEC, cfg.HF_CACHE_SEC))
_tropo_cache = TTLCache(lambda: cfg.TROPO_CACHE_SEC)


def parse_hamqsl(xml_bytes: bytes) -> dict:
    root = ET.fromstring(xml_bytes)
    sd = root.find("solardata")
    if sd is None:
        raise ValueError("no solardata in feed")

    def text(tag: str) -> str:
        return (sd.findtext(tag) or "").strip()
    hf = {(b.get("name"), b.get("time")): (b.text or "").strip()
          for b in sd.iter("band") if b.get("name")}
    vhf = {(p.get("name"), p.get("location")): (p.text or "").strip() for p in sd.iter("phenomenon")}
    return {"sfi": text("solarflux"), "sunspots": text("sunspots"), "a": text("aindex"),
            "k": text("kindex"), "noise": text("signalnoise"), "geomag": text("geomagfield"),
            "hf": hf, "vhf": vhf}


def _fetch_hamqsl_sync() -> dict:
    cached = _hamqsl_cache.get("hamqsl")
    if cached is not None:
        return cached
    data = parse_hamqsl(net.get(HAMQSL_URL, accept="application/xml, text/xml"))
    _hamqsl_cache.put("hamqsl", data)
    return data


def is_daytime(lat: float, lon: float, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(cfg.TIMEZONE)
    rise, set_, state = sun_times(lat, lon, now.date())
    if state:
        return state == "up"
    return rise <= now < set_


def format_hf(data: dict, day: bool, budget: Optional[int] = None) -> str:
    """'📻 HF day: 80-40m🟡 30-20m🟢 17-15m🟡 12-10m🔴 | ☀️SFI 112 🧲K2 A19 | 🔊S1-S2 | N0NBH'.
    Plain text: '80-40m Fair | 30-20m Good | ...'."""
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    when = "day" if day else "night"
    states = [(label, data["hf"].get((name, when)) or "?") for name, label in HF_BANDS]
    sfi, k, a = data["sfi"] or "?", data["k"] or "?", data["a"] or "?"
    noise = data.get("noise")
    if cfg.USE_EMOJI:
        head = f"📻 HF {when}: "
        bands = " ".join(f"{label}{BAND_EMOJI.get(s.lower(), '❔')}" for label, s in states)
        indices, noise_text = f"☀️SFI {sfi} 🧲K{k} A{a}", f"🔊{noise}"
    else:
        head = f"HF {when}: "
        bands = " | ".join(f"{label} {s}" for label, s in states)
        indices, noise_text = f"SFI {sfi} K{k} A{a}", f"Noise {noise}"
    text = f"{head}{bands} | {indices} | {HAMQSL_CREDIT}"
    with_noise = f"{head}{bands} | {indices} | {noise_text} | {HAMQSL_CREDIT}"
    if noise and len(with_noise.encode("utf-8")) <= budget:
        return with_noise
    return text


def tropo_level(gradient: float) -> str:
    for limit, name in TROPO_LEVELS:
        if gradient > limit:
            return name
    return TROPO_LEVELS[-1][1]


def refractivity(t_c: float, rh: float, p_hpa: float) -> float:
    """Radio refractivity N (ITU-R P.453) from temperature (°C), humidity (%) and pressure (hPa)."""
    t_k = t_c + 273.15
    e = rh / 100 * 6.112 * math.exp(17.62 * t_c / (243.12 + t_c))     # water vapour pressure, hPa
    return 77.6 / t_k * (p_hpa + 4810 * e / t_k)


def tropo_gradients(data: dict) -> list[tuple[datetime, float]]:
    """(local time, refractivity gradient in N/km) for each hour, surface to 925 hPa.
    Hours with missing values, or ground at or above the 925 hPa level, are left out."""
    h = data.get("hourly") or {}
    elevation = data.get("elevation") or 0.0
    out = []
    for i, t in enumerate(h.get("time") or []):
        try:
            vals = [h[f][i] for f in TROPO_FIELDS]
        except (KeyError, IndexError):
            continue
        if any(v is None for v in vals):
            continue
        t2, rh2, ps, t925, rh925, z925 = vals
        dz_km = (z925 - elevation) / 1000
        if dz_km < 0.1:
            continue
        grad = (refractivity(t925, rh925, 925) - refractivity(t2, rh2, ps)) / dz_km
        out.append((datetime.fromisoformat(t), grad))
    return out


def _fetch_tropo_sync(lat: float, lon: float) -> dict:
    key = (round(lat, 2), round(lon, 2))
    cached = _tropo_cache.get(key)
    if cached is not None:
        return cached
    params = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(TROPO_FIELDS),
              "forecast_days": 2, "timezone": str(cfg.TIMEZONE)}
    data = net.get_json(f"{TROPO_API}?{urllib.parse.urlencode(params)}")
    _tropo_cache.put(key, data)
    return data


def tropo_outlook(data: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """Now and the strongest refraction in the next 24 hours, or None without data."""
    now = (now or datetime.now(cfg.TIMEZONE)).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    hours = [(t, g) for t, g in tropo_gradients(data) if now <= t < now + timedelta(hours=24)]
    if not hours:
        return None
    best = min(hours, key=lambda h: h[1])
    return {"now": hours[0][1], "best": best[1], "best_at": best[0]}


def format_uhf(outlook: Optional[dict], label: str) -> str:
    """'📶 Norwich UHF tropo: ➖Normal now (-42 N/km) | 24h best ⏫Enhanced Fri 03h (-95) | Open-Meteo'."""
    head = f"📶 {label} UHF tropo: " if cfg.USE_EMOJI else f"{label} UHF tropo: "
    if not outlook:
        return f"{head}no forecast data | {AIR_CREDIT}"
    now_level, best_level = tropo_level(outlook["now"]), tropo_level(outlook["best"])

    def shown(level: str) -> str:
        return TROPO_EMOJI.get(level, "") + level if cfg.USE_EMOJI else level
    text = f"{head}{shown(now_level)} now ({outlook['now']:.0f} N/km)"
    if best_level != now_level:
        text += f" | 24h best {shown(best_level)} {outlook['best_at']:%a %Hh} ({outlook['best']:.0f})"
    else:
        text += " | next 24h similar"
    return f"{text} | {AIR_CREDIT}"


def format_vhf(data: dict, tropo: Optional[str], budget: Optional[int] = None) -> str:
    """'📡 VHF: 6m Es🔴 4m Es🔴 2m Es🟢 144MHz ES Aurora🔴 | Tropo ➖Normal | N0NBH, Open-Meteo'.
    Closed shows 🔴, open shows 🟢 and what N0NBH reports. Plain text keeps the words."""
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    items = []
    for key, label in VHF_PHENOMENA:
        state = (data["vhf"].get(key) or "?").replace("Band ", "")
        if not cfg.USE_EMOJI:
            items.append(f"{label} {state}")
        elif state.lower() == "closed":
            items.append(f"{label}🔴")
        else:
            items.append(f"{label}🟢 {state}" if state != "?" else f"{label}❔")
    sep = " " if cfg.USE_EMOJI else " | "
    head = "📡 VHF: " if cfg.USE_EMOJI else "VHF: "
    credit = HAMQSL_CREDIT + (f", {AIR_CREDIT}" if tropo else "")
    tropo_text = f"Tropo {TROPO_EMOJI.get(tropo, '')}{tropo}" if cfg.USE_EMOJI else f"Tropo {tropo}"
    text = f"{head}{sep.join(items)} | {tropo_text} | {credit}" if tropo else ""
    if not text or len(text.encode("utf-8")) > budget:   # keep the N0NBH data, drop the tropo hint
        text = f"{head}{sep.join(items)} | {HAMQSL_CREDIT}"
    return text


async def _default_latlon() -> Optional[tuple[float, float, str]]:
    try:
        return await asyncio.to_thread(geo._geocode_sync, "")
    except Exception as ex:
        _LOGGER.warning("DEFAULT_LOCATION lookup failed: %s", ex)
        return None


async def get_hf(budget: Optional[int] = None) -> str:
    try:
        data = await asyncio.to_thread(_fetch_hamqsl_sync)
    except Exception as ex:
        _LOGGER.warning("N0NBH solar feed failed: %s", ex)
        return "HF: lookup failed"
    where = await _default_latlon()
    day = is_daytime(where[0], where[1]) if where else 6 <= datetime.now(timezone.utc).hour < 18
    return format_hf(data, day, budget=budget)


async def get_uhf(query: str, quiet: bool = False) -> Optional[str]:
    try:
        lat, lon, label = await asyncio.to_thread(geo._geocode_sync, query)
    except LocationError as ex:
        _LOGGER.info("Location lookup failed for %r: %s", query, ex)
        return None if quiet else f"UHF: {ex}"
    except Exception as ex:
        _LOGGER.warning("Place lookup failed for %r: %s", query, ex)
        return "UHF: place lookup failed"
    try:
        data = await asyncio.to_thread(_fetch_tropo_sync, lat, lon)
    except Exception as ex:
        _LOGGER.warning("Open-Meteo tropo forecast failed for %s: %s", label, ex)
        return "UHF: lookup failed"
    return format_uhf(tropo_outlook(data), label)


async def get_vhf(budget: Optional[int] = None) -> str:
    try:
        data = await asyncio.to_thread(_fetch_hamqsl_sync)
    except Exception as ex:
        _LOGGER.warning("N0NBH solar feed failed: %s", ex)
        return "VHF: lookup failed"
    tropo = None
    where = await _default_latlon()
    if where:
        try:
            outlook = tropo_outlook(await asyncio.to_thread(_fetch_tropo_sync, where[0], where[1]))
            tropo = tropo_level(outlook["now"]) if outlook else None
        except Exception as ex:
            _LOGGER.warning("Open-Meteo tropo forecast failed: %s", ex)
    return format_vhf(data, tropo, budget=budget)
