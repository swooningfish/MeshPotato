"""Sun and moon (worked out locally, no API calls)."""

import asyncio
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import config as cfg
from . import geo
from .geo import LocationError

_LOGGER = logging.getLogger("meshpotato_bot")

J2000 = 2451545.0
J2000_UTC = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)


def _jd(dt: datetime) -> float:
    return J2000 + (dt - J2000_UTC).total_seconds() / 86400


def _from_jd(jd: float) -> datetime:
    return J2000_UTC + timedelta(days=jd - J2000)


def sun_times(lat: float, lon: float, day: date,
              altitude: float = -0.833) -> tuple[Optional[datetime], Optional[datetime], str]:
    """(sunrise, sunset, state) for a local date, from the sunrise equation (about 1 minute accurate).
    state is "" normally, "up" when the sun never sets and "down" when it never rises."""
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=cfg.TIMEZONE)
    n = round(_jd(noon.astimezone(timezone.utc)) - J2000 + lon / 360)   # nearest solar noon
    j_star = n - lon / 360
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360)
    c = 1.9148 * math.sin(m) + 0.0200 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    transit = J2000 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    decl = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cos_w = ((math.sin(math.radians(altitude)) - math.sin(phi) * math.sin(decl))
             / (math.cos(phi) * math.cos(decl)))
    if cos_w < -1:
        return None, None, "up"
    if cos_w > 1:
        return None, None, "down"
    w = math.degrees(math.acos(cos_w)) / 360
    return (_from_jd(transit - w).astimezone(cfg.TIMEZONE),
            _from_jd(transit + w).astimezone(cfg.TIMEZONE), "")


def _hm_length(td: timedelta) -> str:
    mins = int(td.total_seconds() // 60)
    return f"{mins // 60}h{mins % 60:02d}m"


def format_sun(lat: float, lon: float, label: str, day: Optional[date] = None) -> str:
    day = day or datetime.now(cfg.TIMEZONE).date()
    rise, set_, state = sun_times(lat, lon, day)
    head = f"{label} {day:%a %d %b}"
    if state:
        msg = "sun up all day" if state == "up" else "sun down all day"
        return f"☀️ {head}: {msg}" if cfg.USE_EMOJI else f"{head}: {msg}"
    length = _hm_length(set_ - rise)
    if cfg.USE_EMOJI:
        return f"{head} 🌅 {rise:%H:%M} 🌇 {set_:%H:%M} ☀️ {length} daylight"
    return f"{head}: sunrise {rise:%H:%M} sunset {set_:%H:%M}, {length} daylight"


async def get_sun(query: str, quiet: bool = False) -> Optional[str]:
    try:
        lat, lon, label = await asyncio.to_thread(geo._geocode_sync, query)
    except LocationError as ex:
        _LOGGER.info("Location lookup failed for %r: %s", query, ex)
        return None if quiet else f"SUN: {ex}"
    except Exception as ex:
        _LOGGER.warning("Place lookup failed for %r: %s", query, ex)
        return "SUN: place lookup failed"
    return format_sun(lat, lon, label)


MOON_PHASES = [
    ("🌑", "New moon"), ("🌒", "Waxing crescent"), ("🌓", "First quarter"), ("🌔", "Waxing gibbous"),
    ("🌕", "Full moon"), ("🌖", "Waning gibbous"), ("🌗", "Last quarter"), ("🌘", "Waning crescent"),
]
MOON_PRINCIPAL_DEG = 12         # within this of new, quarter or full counts as that phase (about a day)


def moon_elongation(dt: datetime) -> float:
    """Moon's ecliptic longitude minus the sun's, 0-360 degrees (0 new, 180 full).
    Low-precision series, good to about 0.3 degrees (under an hour of phase)."""
    d = _jd(dt) - J2000
    rad = math.radians
    g = rad(357.528 + 0.9856003 * d)
    sun = 280.460 + 0.9856474 * d + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)
    mm = rad(134.963 + 13.064993 * d)
    dd = rad(297.850 + 12.190749 * d)
    ff = rad(93.272 + 13.229350 * d)
    moon = (218.316 + 13.176396 * d + 6.289 * math.sin(mm) - 1.274 * math.sin(mm - 2 * dd)
            + 0.658 * math.sin(2 * dd) + 0.214 * math.sin(2 * mm) - 0.186 * math.sin(g)
            - 0.114 * math.sin(2 * ff))
    return (moon - sun) % 360


def moon_phase(dt: datetime) -> tuple[int, int]:
    """(index into MOON_PHASES, percent of the disc lit)."""
    e = moon_elongation(dt)
    lit = round((1 - math.cos(math.radians(e))) / 2 * 100)
    nearest = round(e / 90) % 4
    if abs((e - nearest * 90 + 180) % 360 - 180) <= MOON_PRINCIPAL_DEG:
        return nearest * 2, lit
    return int(e // 90) * 2 + 1, lit


def next_moon_phase(dt: datetime, target_deg: float) -> datetime:
    """Next time the elongation reaches target_deg (0 new, 180 full)."""
    def offset(t: datetime) -> float:
        return (moon_elongation(t) - target_deg + 180) % 360 - 180
    step = timedelta(hours=6)
    a = dt
    for _ in range(31 * 4):
        b = a + step
        if offset(a) < 0 <= offset(b):
            for _ in range(20):
                mid = a + (b - a) / 2
                if offset(mid) < 0:
                    a = mid
                else:
                    b = mid
            return b
        a = b
    return a


def format_moon(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(cfg.TIMEZONE)
    idx, lit = moon_phase(now)
    emoji, name = MOON_PHASES[idx]
    full = next_moon_phase(now, 180).astimezone(cfg.TIMEZONE)
    new = next_moon_phase(now, 0).astimezone(cfg.TIMEZONE)
    if cfg.USE_EMOJI:
        return f"{emoji} {name}, {lit}% lit | 🌕 Full {full:%a %d %b} | 🌑 New {new:%a %d %b}"
    return f"Moon: {name}, {lit}% lit. Full {full:%a %d %b}, new {new:%a %d %b}"
