"""Aurora and space weather (AuroraWatch UK, Lancaster University, free, no key).

Terms: non-commercial use, credit AuroraWatch UK, no more than one request per
3 minutes, keep their level names and colours. https://aurorawatch.lancs.ac.uk/api-info/
"""

import asyncio
import logging
import xml.etree.ElementTree as ET

from . import config as cfg
from . import net
from .net import TTLCache

_LOGGER = logging.getLogger("meshpotato_bot")

AURORA_API = "https://aurorawatch-api.lancs.ac.uk/0.2/status/"
AURORA_STATUS_URL = AURORA_API + "current-status.xml"
AURORA_ACTIVITY_URL = AURORA_API + "project/awn/sum-activity.xml"
AURORA_HEADERS = {"Referer": "https://github.com/swooningfish/MeshPotato"}
AURORA_MIN_CACHE_SEC = 180      # the API asks for at least 3 minutes between requests
# Level -> (emoji, AuroraWatch UK's own description)
AURORA_LEVELS = {
    "green": ("🟢", "No significant activity"),
    "yellow": ("🟡", "Minor geomagnetic activity"),
    "amber": ("🟠", "Amber alert: possible aurora"),
    "red": ("🔴", "Red alert: aurora likely"),
}
AURORA_CREDIT = "AuroraWatch UK"

_aurora_cache = TTLCache(lambda: max(AURORA_MIN_CACHE_SEC, cfg.AURORA_CACHE_SEC))


def parse_aurora_status(xml_bytes: bytes) -> str:
    site = ET.fromstring(xml_bytes).find("site_status")
    return (site.get("status_id") if site is not None else "") or ""


def parse_aurora_activity(xml_bytes: bytes) -> list[float]:
    """Hourly disturbance in nT, oldest first. The last value is the hour so far."""
    values = []
    for a in ET.fromstring(xml_bytes).iter("activity"):
        try:
            values.append(float(a.findtext("value") or ""))
        except ValueError:
            continue
    return values


def _fetch_aurora_sync() -> dict:
    cached = _aurora_cache.get("aurora")
    if cached is not None:
        return cached
    accept = "application/xml, text/xml"
    status = parse_aurora_status(net.get(AURORA_STATUS_URL, AURORA_HEADERS, accept=accept))
    try:
        activity = parse_aurora_activity(net.get(AURORA_ACTIVITY_URL, AURORA_HEADERS, accept=accept))
    except Exception as ex:         # the level alone is still worth a reply
        _LOGGER.warning("AuroraWatch activity feed failed: %s", ex)
        activity = []
    data = {"status": status, "activity": activity}
    _aurora_cache.put("aurora", data)
    return data


def format_aurora(data: dict) -> str:
    """'🟢 Green: No significant activity | 27nT now, 62nT peak 24h | AuroraWatch UK'."""
    level = (data.get("status") or "").lower()
    emoji, description = AURORA_LEVELS.get(level, ("❔", "Status unknown"))
    parts = [f"{emoji} {level.capitalize()}: {description}" if cfg.USE_EMOJI and level in AURORA_LEVELS
             else f"Aurora {level.capitalize() or '?'}: {description}"]
    activity = data.get("activity") or []
    if activity:
        parts.append(f"{activity[-1]:.0f}nT now, {max(activity[-24:]):.0f}nT peak 24h")
    parts.append(AURORA_CREDIT)
    return " | ".join(parts)


async def get_aurora() -> str:
    try:
        return format_aurora(await asyncio.to_thread(_fetch_aurora_sync))
    except Exception as ex:
        _LOGGER.warning("AuroraWatch lookup failed: %s", ex)
        return "AURORA: lookup failed"
