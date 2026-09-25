"""Positions, distances and bearings, and geocoding with postcodes.io (free, UK only, no key)."""

import asyncio
import logging
import re
import math
import urllib.error
import urllib.parse
from typing import Any, Optional

from . import config as cfg
from . import state
from . import net
from .text import names_list
from .contacts import CHAT_NODE_TYPE, contact_name, contacts_by_key, find_contacts
from .rx import hops_text
from .net import TTLCache

_LOGGER = logging.getLogger("meshpotato_bot")

# ---------- !dist: distance along the path ----------
EARTH_RADIUS_KM = 6371.0
KM_PER_MILE = 1.609344


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def gps_of(node: Optional[dict]) -> Optional[tuple[float, float]]:
    """(lat, lon) from a contact or self_info, None when the node shares no position (0, 0)."""
    if not node:
        return None
    lat, lon = node.get("adv_lat") or 0.0, node.get("adv_lon") or 0.0
    return (lat, lon) if (lat, lon) != (0.0, 0.0) else None


def repeater_position(node: str, contacts: dict) -> Optional[tuple[float, float]]:
    """Position of the one repeater whose key starts with the path hash. None when no
    repeater or several match, because the bot can't tell which one it was."""
    matches = [c for c in contacts_by_key(contacts, node) if c.get("type") != CHAT_NODE_TYPE]
    return gps_of(matches[0]) if len(matches) == 1 else None


def sender_position(contacts: dict, name: str = "", key_prefix: str = "") -> Optional[tuple[float, float]]:
    """Sender's advertised position: by key in a DM, by exact name in a channel (only if one matches)."""
    if key_prefix:
        matches = contacts_by_key(contacts, key_prefix)
    elif name:
        matches = [c for c in contacts.values() if (c.get("adv_name") or "").strip().lower() == name.lower()]
    else:
        return None
    return gps_of(matches[0]) if len(matches) == 1 else None


def bot_position(self_info: Optional[dict] = None) -> Optional[tuple[float, float]]:
    """The bot radio's own advertised position, else DEFAULT_LOCATION if it is in LOCATIONS."""
    if self_info is None:
        self_info = getattr(state.radio, "self_info", None) or {}
    pos = gps_of(self_info)
    if pos:
        return pos
    for name, latlon in cfg.LOCATIONS.items():
        if name.lower() == cfg.DEFAULT_LOCATION.strip().lower():
            return latlon
    return None


def distance_text(km: float) -> str:
    value, unit = (km / KM_PER_MILE, "mi") if cfg.DIST_MILES else (km, "km")
    return f"{value:.1f}{unit}" if value < 10 else f"{value:.0f}{unit}"


def format_dist(info: dict[str, Any], contacts: dict, start: Optional[tuple[float, float]],
                end: Optional[tuple[float, float]], budget: Optional[int] = None) -> str:
    """!dist: '📏 You ›4.2km› a1 ›?› b2 ›3.1km› Bot | 7.3km+, 1 of 3 legs unknown | 5.0km direct'.
    Each leg needs a position at both ends. Drops the leg list, then the direct line, to fit."""
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    icon = "📏 " if cfg.USE_EMOJI else "Dist: "
    if info.get("direct") or not info.get("path_len"):
        how = "Direct route" if info.get("direct") else "Heard directly"
        if start and end:
            return f"{icon}{how}, you are {distance_text(haversine_km(start, end))} away"
        return f"{icon}{how}, your position isn't known" if not start else f"{icon}{how}, bot position not set"
    nodes = info.get("path_nodes") or []
    if not nodes:
        return f"{icon}{hops_text(info['path_len'])}, path not reported"
    points = [("You", start)] + [(n, repeater_position(n, contacts)) for n in nodes] + [("Bot", end)]
    legs = [haversine_km(a[1], b[1]) if a[1] and b[1] else None for a, b in zip(points, points[1:])]
    known = [km for km in legs if km is not None]
    if not known:
        return f"{icon}{hops_text(len(nodes))}, no positions known along the path"
    arrow = "›" if cfg.USE_EMOJI else ">"
    chain = points[0][0] + "".join(f" {arrow}{distance_text(km) if km is not None else '?'}{arrow} {p[0]}"
                                   for km, p in zip(legs, points[1:]))
    unknown = len(legs) - len(known)
    total = f"{distance_text(sum(known))}" + (f"+, {unknown} of {len(legs)} legs unknown" if unknown else " total")
    straight = f"{distance_text(haversine_km(start, end))} direct" if start and end else ""
    for parts in ([chain, total, straight], [chain, total], [f"{hops_text(len(nodes))}: {total}", straight],
                  [f"{hops_text(len(nodes))}: {total}"]):
        text = icon + " | ".join(p for p in parts if p)
        if len(text.encode("utf-8")) <= budget:
            return text
    return text


# ---------- !bearing: distance and direction to a contact or place ----------
def initial_bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Compass bearing in degrees from a to b along the great circle."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return math.degrees(math.atan2(y, x)) % 360


async def get_bearing(query: str, contacts: dict, start: Optional[tuple[float, float]]) -> str:
    """!bearing: '🧭 Alice: 2.3km NE (48°) from you'. Measures from the sender when their
    position is known, else from the bot. A name that isn't a contact is tried as a place."""
    icon = "🧭 " if cfg.USE_EMOJI else "Bearing: "
    q = query.strip()
    if not q:
        return icon + "Use !bearing <name or place>, e.g. !bearing Alice, !bearing NR1, !bearing 52.6,1.3"
    origin, frm = (start, "you") if start else (bot_position(), "the bot")
    if not origin:
        return icon + "Your position isn't known and the bot has none set"
    matches = find_contacts(contacts, q)
    if len(matches) > 1:
        return f"{icon}{len(matches)} contacts match '{q}': {names_list([contact_name(c) for c in matches])}"
    if matches:
        label, pos = contact_name(matches[0]), gps_of(matches[0])
        if not pos:
            return f"{icon}{label} doesn't share a position"
    else:
        try:
            lat, lon, label = await asyncio.to_thread(_geocode_sync, q)
        except LocationError:
            return f"{icon}No contact or place called '{q}'"
        except Exception as ex:
            _LOGGER.warning("Place lookup failed for %r: %s", q, ex)
            return f"{icon}'{q}' isn't a contact and the place lookup failed"
        pos = (lat, lon)
    km = haversine_km(origin, pos)
    if km < 0.05:
        return f"{icon}{label} is right by {frm}"
    deg = initial_bearing(origin, pos)
    return f"{icon}{label}: {distance_text(km)} {compass_point(deg)} ({deg:.0f}°) from {frm}"


FULL_POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.I)
OUTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?$", re.I)
LATLON_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

geocode_cache = TTLCache(lambda: cfg.GEOCODE_CACHE_SEC)


class LocationError(Exception):
    """The place can't be found. Commands stay quiet about these."""


# Bigger places win when several share a name ("Brighton" the city, not the hamlet in Cornwall)
PLACE_TYPE_RANK = {"City": 0, "Town": 1, "Other Settlement": 2, "Suburban Area": 3, "Village": 4, "Hamlet": 5}


def _best_place(results: list[dict], query: str) -> dict:
    """Pick the biggest place whose name matches exactly, else postcodes.io's first result."""
    q = query.strip().lower()
    exact = [r for r in results
             if q in ((r.get("name_1") or "").lower(), (r.get("name_2") or "").lower())]
    if not exact:
        return results[0]
    return min(exact, key=lambda r: PLACE_TYPE_RANK.get(r.get("local_type"), len(PLACE_TYPE_RANK)))


def _geocode_sync(query: str) -> tuple[float, float, str]:
    q = query.strip() or cfg.DEFAULT_LOCATION
    key = q.lower()

    named = {k.lower(): k for k in cfg.LOCATIONS}
    if key in named:
        name = named[key]
        lat, lon = cfg.LOCATIONS[name]
        return lat, lon, name.title()

    m = LATLON_RE.match(q)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon, f"{lat:.2f},{lon:.2f}"
        raise LocationError("bad lat,lon")

    cached = geocode_cache.get(key)
    if cached:
        return cached

    base = "https://api.postcodes.io"
    try:
        if FULL_POSTCODE_RE.match(q):
            data = net.get_json(f"{base}/postcodes/{urllib.parse.quote(q)}")
            r = data["result"]
            out = (r["latitude"], r["longitude"], r["postcode"])
        elif OUTCODE_RE.match(q):
            data = net.get_json(f"{base}/outcodes/{urllib.parse.quote(q)}")
            r = data["result"]
            out = (r["latitude"], r["longitude"], r["outcode"])
        else:
            data = net.get_json(f"{base}/places?q={urllib.parse.quote(q)}&limit=20")
            res = data.get("result") or []
            if not res:
                raise LocationError(f"'{q}' not found")
            r = _best_place(res, q)
            name = r.get("name_2") if (r.get("name_2") or "").lower() == q.lower() else r.get("name_1")
            out = (r["latitude"], r["longitude"], name or q.title())
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            raise LocationError(f"'{q}' not found")
        raise
    if out[0] is None or out[1] is None:
        raise LocationError(f"'{q}' has no coordinates")
    geocode_cache.put(key, out)
    return out


COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def compass_point(deg: Optional[float]) -> str:
    if deg is None:
        return "?"
    return COMPASS[int((deg % 360) / 45 + 0.5) % 8]
