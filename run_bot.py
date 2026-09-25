#!/usr/bin/env python3
"""
MeshPotato bot (serial companion radio) for use on MeshCore

Built on the patterns in meshcore_py/examples/serial_pingbot.py and meshcore_py/examples/serial_rss_bot.py.

Commands (channel or direct message):
  ping               -> Pong with hop count         (whole message, a leading ! is optional)
  test               -> RX in DEFAULT_LOCATION, hop count, SNR, RSSI and your distance (same rules as ping)
  !path / !trace     -> The repeaters your message came through, with names where known
  !dist              -> Distance of each leg along that path, for repeaters with a known position
  !wx [location]     -> Current conditions from Met Office DataHub (hourly)
  !wxh [location]    -> Next few hours, hour by hour (hourly)
  !wxf [location]    -> 3-day forecast from Met Office DataHub (daily)
  !warn [location]   -> Met Office weather warnings for the region, then posts changes for a while
  !sun [location]    -> Sunrise, sunset and hours of daylight today
  !moon              -> Moon phase, % lit and the next full and new moon
  !aurora / !solar   -> Geomagnetic activity and aurora alert level from AuroraWatch UK
  !aq [location]     -> Air quality index and pollutants from Open-Meteo
  !pollen [location] -> Pollen forecast for the next 24 hours from Open-Meteo
  !hf / !bands       -> HF band conditions, day or night, with SFI and K/A index (N0NBH)
  !vhf               -> 6m/4m/2m E-skip, VHF aurora (N0NBH) and tropo at DEFAULT_LOCATION
  !uhf [location]    -> UHF tropospheric refraction now and the best in 24 hours (Open-Meteo)
  !stats             -> Commands served, messages heard, Met Office calls used (admin DMs only)
  !uptime            -> How long the bot (and the computer) has been up (admin DMs only)
  !mute <minutes>    -> Stop replies and scheduled messages for a while (admin DMs only)
  !unmute            -> End a mute early (admin DMs only)
  !say <ch> <text>   -> Post text to a channel as the bot (admin DMs only)
  !save              -> Write the !who / !status heard list to disk now (admin DMs only)
  !help              -> Help topics: !helptest, !helpwx, !helpradio, !helpfun, !helpconv (the ! is required)
  !roll [NdS+M]      -> Roll dice: !roll, !roll d20, !roll 2d6+3
  !flipacoin         -> Heads or tails
  !eightball <q>     -> Ask the eight ball a yes/no question
  !conv <n> <unit> [unit] -> Unit conversion: !conv 10 mi km, !conv 20 c, !conv 868 mhz
  !ohm <two of V/A/Ω/W>   -> Ohm's law and power: !ohm 12v 2a, !ohm 5v 220r, !ohm 10w 50ohm
  !res <colours|value>    -> Resistor colour code both ways: !res yellow violet red gold, !res 4k7
  !who [hours|rpt]        -> People (or repeaters) heard lately, most recent first
  !status <name>          -> When the bot last heard someone, how, and where they are
  !bearing <name|place>   -> Distance and compass direction from you (or the bot) to a contact or place
  !freq [topic]           -> Frequency lists: pmr, cb, ham, hf, marine, air, and mesh (the bot's radio)

[location] accepts:
  - a name from LOCATIONS below        (!wx home)
  - a UK postcode or outward code      (!wx LS1 4AP, !wx LS1)
  - a UK place name                    (!wx Harrogate)
  - decimal lat,lon                    (!wx 53.80,-1.55)
  - nothing                            (uses DEFAULT_LOCATION)

Features:
  - Global, per-user and per-channel sliding-window rate limits
  - Outgoing transmit queue with a minimum gap between radio sends
  - Scheduled messages: daily times, weekday filters, one-shot timestamps, intervals
  - Weather and geocode caching plus a daily call budget for the Met Office free tier
  - Weather warning alerts: after a !warn, changes to that region's warnings are posted
  - Settings can be overridden from config.toml (Python 3.11+)

Setup:
  pip install meshcore
  Get a free "Site Specific" API key at https://datahub.metoffice.gov.uk/
  Windows:  set METOFFICE_API_KEY=your_key
  Linux:    export METOFFICE_API_KEY=your_key
  python run_bot.py --port COM4
"""

import argparse
import asyncio
import json
import logging
import os
import random
import re
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

try:
    import tomllib                  # Python 3.11+
except ModuleNotFoundError:         # Python 3.10: config.toml is not supported
    tomllib = None

from meshcore import MeshCore, EventType

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# =====================================================================
# Config
# Defaults below. Override any of them in config.toml (see config.example.toml)
# using the same names in lower case.
# =====================================================================
SERIAL_PORT = "/dev/ttyACM0"    # override with --port
BAUDRATE = 115200

CHANNEL_IDXS = [1, 3]           # channels the bot listens and replies on
ANSWER_DMS = True               # reply to direct messages as well
TIMEZONE = ZoneInfo("Europe/London")
LOG_LEVEL = "INFO"              # DEBUG also logs every message that isn't a command

METOFFICE_API_KEY = ""          # set in config.toml, never here. See _load_api_key for the lookup order
WX_CACHE_SEC = 1800             # reuse a forecast for 30 min
WX_DAILY_CALL_BUDGET = 300      # Met Office calls per UTC day (free tier: 360)
GEOCODE_CACHE_SEC = 86400
HTTP_TIMEOUT = 20

DEFAULT_LOCATION = "Norwich"
LOCATIONS: dict[str, tuple[float, float]] = {
    "Norwich": (52.6278, 1.2983),     # Norwich, change to your location
    "Cambridge": (52.205276, 0.119167),
    "Ipswich": (52.0592, 1.1555),
}

MAX_REPLY_BYTES = 135           # MeshCore limits text by UTF-8 bytes (~160 incl. "name: ")
USE_EMOJI = True                # False = plain ASCII replies
USE_MPH = True                  # False = m/s
DIST_MILES = False              # !dist in miles instead of km
WXH_HOURS = 6                   # wxh: max hours to show (cut to fit MAX_REPLY_BYTES)
WXH_STEP_HOURS = 1              # wxh: 1 = every hour, 2 = every 2 hours, 3 = every 3 hours
WXH_MENTION_RESERVE = 25        # wxh: bytes kept free for text around {wxh} in schedules
WARN_CACHE_SEC = 600            # reuse the Met Office warnings feed for 10 min
WARN_WATCH_HOURS = 24           # after a !warn, post changes to that region's warnings for this long
WARN_WATCH_MAX = 10             # most regions and channels watched at once
WARN_WATCH_TICK_SEC = 60        # how often watched regions are checked
AURORA_CACHE_SEC = 300          # reuse AuroraWatch UK data for 5 min (never less than 3 min, their rule)
AIR_CACHE_SEC = 3600            # reuse Open-Meteo air quality and pollen for a place for 1 hour
HF_CACHE_SEC = 3600             # reuse the N0NBH HF/VHF feed for 1 hour (never less, their request)
TROPO_CACHE_SEC = 3600          # reuse a place's Open-Meteo tropo forecast for 1 hour
# Pollen count thresholds in grains/m³: [moderate from, high from, very high from].
# Grass uses the Met Office scale. Types not listed show the count only.
POLLEN_LEVELS: dict[str, list[float]] = {"grass": [30, 50, 150]}

# ---------- Rate limits: (max_events, window_seconds) ----------
RATE_LIMIT_GLOBAL = (20, 60)        # all commands across the bot
RATE_LIMIT_PER_USER = (3, 60)       # per pubkey (DM) or per sender name (channel)
RATE_LIMIT_PER_CHANNEL = (8, 60)    # per channel index ("dm" bucket for DMs)
RATE_LIMIT_NOTIFY = True            # tell a user once per window when they hit a limit
MIN_TX_GAP_SEC = 3.0                # minimum seconds between any two radio sends
MUTE_MAX_MINUTES = 1440             # longest !mute (24 hours)
ADMIN_PUBKEYS: set[str] = set()     # pubkey prefixes (12 hex chars) exempt from limits and
                                    # allowed the admin commands, DMs only

# ---------- Fun commands ----------
ROLL_MAX_DICE = 10
ROLL_MAX_SIDES = 1000
EIGHTBALL_ANSWERS = [
    # yes
    "Yes, without a doubt.", "Signs point to yes.", "The mesh says yes.",
    "Count on it.", "All nodes agree: yes.", "Looks good from here.",
    "Yes, go for it.", "Strong signal on that one. Yes.",
    # unsure
    "Signal too weak. Ask again.", "Packet lost. Try later.",
    "Too many hops to tell.", "The answer is still in flight.",
    "Ask again after the next advert.",
    # no
    "No.", "Not a chance.", "The mesh says no.",
    "Unlikely.", "All nodes disagree.", "Don't count on it.",
]

# ---------- Who's about: !who, !status ----------
WHO_HOURS = 24                  # !who lists people heard in this many hours
HEARD_KEEP_DAYS = 7             # forget a node not heard for this long
HEARD_FILE = "heard.json"       # who was heard when, kept over restarts. Relative = next to run_bot.py

# ---------- Frequencies: !freq <topic> ----------
# config.toml [freq_lists] adds topics or replaces these. An empty string removes one.
# "mesh" is not listed here: it reads the bot radio's own settings.
FREQ_LISTS: dict[str, str] = {
    "pmr": "PMR446 MHz: 1 446.00625, 2 .01875, 3 .03125, 4 .04375, 5 .05625, 6 .06875, 7 .08125, "
           "8 .09375, 9-16 to .19375",
    "cb": "CB UK 27/81 FM: ch1 27.60125 MHz, 10kHz steps, ch9 27.68125 emergency, ch19 27.78125. "
          "EU CEPT: ch9 27.065, ch19 27.185",
    "ham": "Ham FM calling MHz: 2m 145.500, 70cm 433.500, 6m 51.510, 4m 70.450",
    "hf": "HF emergency centres of activity (IARU R1) MHz: 3.760, 7.110, 14.300, 18.160, 21.360",
    "marine": "Marine VHF: ch16 156.800 distress and calling, ch67 156.375 UK small craft safety, "
              "ch70 156.525 DSC only",
    "air": "Air band: 121.500 MHz distress (guard)",
}

# ---------- Scheduled messages ----------
# Each entry needs "text" and a target: "channel": <idx> or "dm": "<pubkey prefix>".
# Timing, pick one:
#   "time": "HH:MM"                 daily, local TIMEZONE
#       optional "days": ["mon", "tue", ...]
#   "at": "YYYY-MM-DD HH:MM"        one-shot local timestamp
#   "at": "MM-DD HH:MM"             same date every year
#   "every_minutes": N              repeating interval, optional "start": "HH:MM"
# Text tokens: {time} {date} {wx} {wx:place} {wxh} {wxh:place} {wxf} {wxf:place}
#              {warn} {warn:place} {sun} {sun:place} {moon} {aurora}
#              {aq} {aq:place} {pollen} {pollen:place} {hf} {vhf} {uhf} {uhf:place}
SCHEDULED_MESSAGES: list[dict[str, Any]] = [
    {"name": "morning-wx", "time": "07:30", "days": ["mon", "tue", "wed", "thu", "fri"],
     "channel": 1, "text": "Morning WX {wx}"},
    {"name": "weekend-fcst", "time": "08:30", "days": ["sat", "sun"],
     "channel": 1, "text": "{wxf}"},
    {"name": "xmas", "at": "12-25 09:00",
     "channel": 1, "text": "Merry Christmas."},
]
SCHEDULE_GRACE_SEC = 120        # fire a slot up to this long after its due time
SCHEDULE_TICK_SEC = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_LOGGER = logging.getLogger("meshpotato_bot")


def _clean_key(v: str) -> str:
    return v.strip().strip('"').strip("'").strip()


def _load_api_key() -> tuple[str, str]:
    """Return (key, where it came from). Lookup order:
    1. METOFFICE_API_KEY environment variable
    2. metoffice_api_key in config.toml
    3. file named by METOFFICE_KEY_FILE
    4. ~/.config/meshcore/metoffice_key
    5. metoffice_key.txt next to this script
    Quotes, spaces and Windows line endings are stripped.
    """
    key = _clean_key(os.environ.get("METOFFICE_API_KEY", ""))
    if key:
        return key, "METOFFICE_API_KEY"
    if METOFFICE_API_KEY:
        return METOFFICE_API_KEY, "config"
    candidates = [
        os.environ.get("METOFFICE_KEY_FILE", ""),
        os.path.expanduser("~/.config/meshcore/metoffice_key"),
        os.path.join(SCRIPT_DIR, "metoffice_key.txt"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    key = _clean_key(fh.read())
                if key:
                    return key, path
            except OSError:
                pass
    return "", ""


# The key in use. load_config() looks it up again once config.toml is read.
MET_OFFICE_API_KEY, MET_OFFICE_KEY_SOURCE = _load_api_key()
MET_OFFICE_BASE = "https://data.hub.api.metoffice.gov.uk/sitespecific/v0/point/"

# =====================================================================
# config.toml
# =====================================================================
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.toml")


def _pair(v: Any) -> tuple[int, float]:
    count, window = v
    return int(count), float(window)


def _log_level(v: Any) -> str:
    level = str(v).upper()
    if not isinstance(logging.getLevelName(level), int):
        raise ValueError(f"unknown level {v!r}, use DEBUG, INFO, WARNING or ERROR")
    return level


# Settings that config.toml may set. A converter turns the TOML value into the
# Python type; None means the value must match the type of the default.
_CONFIG_SETTINGS: dict[str, Optional[Callable[[Any], Any]]] = {
    "METOFFICE_API_KEY": lambda v: _clean_key(str(v)),
    "SERIAL_PORT": None,
    "BAUDRATE": None,
    "CHANNEL_IDXS": lambda v: [int(x) for x in v],
    "ANSWER_DMS": None,
    "TIMEZONE": ZoneInfo,
    "LOG_LEVEL": _log_level,
    "WX_CACHE_SEC": None,
    "WX_DAILY_CALL_BUDGET": None,
    "GEOCODE_CACHE_SEC": None,
    "HTTP_TIMEOUT": None,
    "DEFAULT_LOCATION": None,
    "LOCATIONS": lambda d: {str(k): (float(v[0]), float(v[1])) for k, v in d.items()},
    "MAX_REPLY_BYTES": None,
    "USE_EMOJI": None,
    "USE_MPH": None,
    "DIST_MILES": None,
    "WXH_HOURS": None,
    "WXH_STEP_HOURS": None,
    "WXH_MENTION_RESERVE": None,
    "WARN_CACHE_SEC": None,
    "WARN_WATCH_HOURS": None,
    "WARN_WATCH_MAX": None,
    "WARN_WATCH_TICK_SEC": None,
    "AURORA_CACHE_SEC": None,
    "AIR_CACHE_SEC": None,
    "HF_CACHE_SEC": None,
    "TROPO_CACHE_SEC": None,
    "POLLEN_LEVELS": lambda d: {str(k).lower(): [float(x) for x in v] for k, v in d.items()},
    "RATE_LIMIT_GLOBAL": _pair,
    "RATE_LIMIT_PER_USER": _pair,
    "RATE_LIMIT_PER_CHANNEL": _pair,
    "RATE_LIMIT_NOTIFY": None,
    "MIN_TX_GAP_SEC": float,
    "ADMIN_PUBKEYS": lambda v: {str(k) for k in v},
    "MUTE_MAX_MINUTES": None,
    "ROLL_MAX_DICE": None,
    "ROLL_MAX_SIDES": None,
    "EIGHTBALL_ANSWERS": lambda v: [str(a) for a in v],
    "WHO_HOURS": float,
    "HEARD_KEEP_DAYS": float,
    "HEARD_FILE": None,
    "FREQ_LISTS": lambda d: {k: t for k, t in {**FREQ_LISTS, **{str(k).lower(): str(v) for k, v in d.items()}}.items()
                             if t},
    "SCHEDULED_MESSAGES": lambda v: [dict(e) for e in v],
    "SCHEDULE_GRACE_SEC": None,
    "SCHEDULE_TICK_SEC": None,
}


def load_config(path: Optional[str] = None) -> Optional[str]:
    """Override the settings above from a TOML file.
    Uses `path`, else $MESHPOTATO_CONFIG, else config.toml next to this script.
    Returns the file loaded, or None when there is no config file."""
    explicit = path or os.environ.get("MESHPOTATO_CONFIG")
    path = explicit or CONFIG_FILE
    if not os.path.isfile(path):
        if explicit:
            raise SystemExit(f"Config file not found: {path}")
        return None
    if tomllib is None:
        raise SystemExit(f"{path} needs Python 3.11 or newer (tomllib)")
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as ex:
        raise SystemExit(f"Can't read {path}: {ex}")

    g = globals()
    for key, value in data.items():
        name = key.upper()
        if name not in _CONFIG_SETTINGS:
            _LOGGER.warning("Unknown setting '%s' in %s ignored", key, path)
            continue
        convert = _CONFIG_SETTINGS[name]
        try:
            if convert:
                value = convert(value)
            elif type(value) is not type(g[name]):
                raise TypeError(f"expected {type(g[name]).__name__}, got {type(value).__name__}")
        except Exception as ex:
            raise SystemExit(f"Bad value for '{key}' in {path}: {ex}")
        g[name] = value

    global MET_OFFICE_API_KEY, MET_OFFICE_KEY_SOURCE
    MET_OFFICE_API_KEY, MET_OFFICE_KEY_SOURCE = _load_api_key()
    if MET_OFFICE_KEY_SOURCE == "config":
        MET_OFFICE_KEY_SOURCE = path
    _wx_cache.ttl = WX_CACHE_SEC
    _geo_cache.ttl = GEOCODE_CACHE_SEC
    _warn_cache.ttl = WARN_CACHE_SEC
    _aurora_cache.ttl = max(AURORA_MIN_CACHE_SEC, AURORA_CACHE_SEC)
    _air_cache.ttl = AIR_CACHE_SEC
    _hamqsl_cache.ttl = max(HAMQSL_MIN_CACHE_SEC, HF_CACHE_SEC)
    _tropo_cache.ttl = TROPO_CACHE_SEC
    return path


# =====================================================================
# Path and signal info
# =====================================================================
RX_LOG_MAX_AGE_SEC = 5.0        # an RX log older than this can't be the packet that carried a message
DIRECT_PATH_LEN = 0xFF          # path_len of a message that arrived by direct route

# Last RX_LOG_DATA seen: path_len, path_nodes, snr, rssi and its monotonic time "at"
latest_rx: dict[str, Any] = {}


def _split_path(path_hex: str, hash_size: int) -> list[str]:
    step = max(1, hash_size) * 2
    return [path_hex[i:i + step] for i in range(0, len(path_hex), step)]


def _parse_raw_packet(hex_str: str) -> dict[str, Any]:
    """Read the path from a raw packet: header, [4 transport code bytes], path byte, path.
    The path byte holds the hop count (low 6 bits) and hash size - 1 (top 2 bits)."""
    try:
        data = bytes.fromhex(re.sub(r"\s", "", hex_str))
        i = 1
        if data[0] & 0x03 in (0x00, 0x03):      # transport flood / transport direct
            i += 4
        path_byte = data[i]
        hash_size = (path_byte >> 6) + 1
        path_len = path_byte & 0x3F
        path = data[i + 1:i + 1 + path_len * hash_size]
        if len(path) < path_len * hash_size:
            return {}
        return {"path_len": path_len, "path_nodes": _split_path(path.hex(), hash_size)}
    except (ValueError, IndexError):
        return {}


def parse_rx_log_data(payload: Any) -> dict[str, Any]:
    """Path and signal info from an RX_LOG_DATA payload.
    Uses the fields meshcore has already parsed, else decodes the raw packet."""
    if not isinstance(payload, dict):
        return {}
    if payload.get("path_len") is not None:
        result = {"path_len": payload["path_len"],
                  "path_nodes": _split_path(payload.get("path") or "", payload.get("path_hash_size") or 1)}
    else:
        raw = payload.get("payload")
        result = _parse_raw_packet(raw.hex() if isinstance(raw, bytes) else str(raw or ""))
    for key in ("snr", "rssi"):
        if payload.get(key) is not None:
            result[key] = payload[key]
    return result


def message_rx_info(msg: dict[str, Any]) -> dict[str, Any]:
    """Path and signal info for a received message.
    Fields on the message win. The last RX log only fills gaps when it is recent
    and has the same hop count, so it is most likely the packet that carried it."""
    info: dict[str, Any] = {}
    path_len = msg.get("path_len")
    hash_mode = msg.get("path_hash_mode") or 0
    if path_len == DIRECT_PATH_LEN or (path_len == 0x3F and hash_mode == 3):
        info["direct"] = True
        path_len = None
    rx = latest_rx if time.monotonic() - latest_rx.get("at", 0) <= RX_LOG_MAX_AGE_SEC else {}
    if path_len is not None and rx.get("path_len") not in (None, path_len):
        rx = {}
    if not info:
        info["path_len"] = path_len if path_len is not None else rx.get("path_len")
        if msg.get("path"):
            info["path_nodes"] = _split_path(msg["path"], hash_mode + 1)
        elif rx.get("path_nodes"):
            info["path_nodes"] = rx["path_nodes"]
    info["snr"] = msg.get("SNR", rx.get("snr"))
    info["rssi"] = msg.get("RSSI", rx.get("rssi"))
    return info


def _hops(n: int) -> str:
    return f"{n} hop{'' if n == 1 else 's'}"


def format_hops(info: dict[str, Any]) -> str:
    """ping: '(2 hops)'."""
    if info.get("direct"):
        return "(direct route)"
    n = info.get("path_len")
    return f"({_hops(n)})" if n is not None else "(? hops)"


def format_rx_report(info: dict[str, Any]) -> str:
    """test: '🐸 (2 hops) 📶 SNR 7.5dB 〰️ RSSI -85dBm', plain '(2 hops) SNR 7.5dB RSSI -85dBm'.
    The path addresses are left out, because a long path pushes the reply past MAX_REPLY_BYTES."""
    parts = [("🐸 " if USE_EMOJI else "") + format_hops(info)]
    snr, rssi = ("📶 SNR", "〰️ RSSI") if USE_EMOJI else ("SNR", "RSSI")
    if info.get("snr") is not None:
        parts.append(f"{snr} {info['snr']:g}dB")
    if info.get("rssi") is not None:
        parts.append(f"{rssi} {info['rssi']}dBm")
    return " ".join(parts)


PATH_NAME_CHARS = 12            # repeater names in !path are cut to this many characters
CHAT_NODE_TYPE = 1              # MeshCore contact type for a companion. Companions don't repeat


def repeater_names(contacts: Optional[dict] = None) -> dict[str, str]:
    """Public key (lower-case hex) -> name for the radio's contacts that can repeat."""
    if contacts is None:
        contacts = getattr(_radio, "contacts", None) or {}
    names = {}
    for c in contacts.values():
        key, name = (c.get("public_key") or "").lower(), (c.get("adv_name") or "").strip()
        if key and name and c.get("type") != CHAT_NODE_TYPE:
            names[key] = name
    return names


def _node_label(node: str, names: dict[str, str]) -> str:
    """'a1 Norwich' when exactly one known repeater's key starts with the hash, else 'a1'."""
    matches = [n for key, n in names.items() if key.startswith(node.lower())]
    if len(matches) != 1:
        return node
    return f"{node} {matches[0][:PATH_NAME_CHARS].strip()}"


def format_path(info: dict[str, Any], names: Optional[dict[str, str]] = None,
                budget: Optional[int] = None) -> str:
    """!path: '🛤️ 3 hops: a1 Norwich › b2 › c3', first repeater first.
    Leaves the names out if they don't fit, then the last repeaters ('+2 more')."""
    budget = MAX_REPLY_BYTES if budget is None else budget
    icon, sep = ("🛤️ ", " › ") if USE_EMOJI else ("Path ", " > ")
    if info.get("direct"):
        return f"{icon}Direct route, the path isn't carried in the message"
    n = info.get("path_len")
    if n is None:
        return f"{icon}Path unknown"
    if n == 0:
        return f"{icon}0 hops, heard directly"
    nodes = info.get("path_nodes") or []
    if not nodes:
        return f"{icon}{_hops(n)}, path not reported"
    head = f"{icon}{_hops(n)}: "
    for labels in ([_node_label(x, names or {}) for x in nodes], nodes):
        text = head + sep.join(labels)
        if len(text.encode("utf-8")) <= budget:
            return text
    shown = []
    for i, node in enumerate(nodes):
        more = f" +{len(nodes) - i - 1} more" if i < len(nodes) - 1 else ""
        if len((head + sep.join(shown + [node]) + more).encode("utf-8")) > budget:
            break
        shown.append(node)
    return head + sep.join(shown) + f" +{len(nodes) - len(shown)} more"


# ---------- !dist: distance along the path ----------
EARTH_RADIUS_KM = 6371.0
KM_PER_MILE = 1.609344


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def _gps(node: Optional[dict]) -> Optional[tuple[float, float]]:
    """(lat, lon) from a contact or self_info, None when the node shares no position (0, 0)."""
    if not node:
        return None
    lat, lon = node.get("adv_lat") or 0.0, node.get("adv_lon") or 0.0
    return (lat, lon) if (lat, lon) != (0.0, 0.0) else None


def repeater_position(node: str, contacts: dict) -> Optional[tuple[float, float]]:
    """Position of the one repeater whose key starts with the path hash. None when no
    repeater or several match, because the bot can't tell which one it was."""
    matches = [c for c in contacts.values()
               if (c.get("public_key") or "").lower().startswith(node.lower())
               and c.get("type") != CHAT_NODE_TYPE]
    return _gps(matches[0]) if len(matches) == 1 else None


def sender_position(contacts: dict, name: str = "", key_prefix: str = "") -> Optional[tuple[float, float]]:
    """Sender's advertised position: by key in a DM, by exact name in a channel (only if one matches)."""
    if key_prefix:
        matches = [c for c in contacts.values()
                   if (c.get("public_key") or "").lower().startswith(key_prefix.lower())]
    elif name:
        matches = [c for c in contacts.values() if (c.get("adv_name") or "").strip().lower() == name.lower()]
    else:
        return None
    return _gps(matches[0]) if len(matches) == 1 else None


def bot_position(self_info: Optional[dict] = None) -> Optional[tuple[float, float]]:
    """The bot radio's own advertised position, else DEFAULT_LOCATION if it is in LOCATIONS."""
    if self_info is None:
        self_info = getattr(_radio, "self_info", None) or {}
    pos = _gps(self_info)
    if pos:
        return pos
    for name, latlon in LOCATIONS.items():
        if name.lower() == DEFAULT_LOCATION.strip().lower():
            return latlon
    return None


def _distance(km: float) -> str:
    value, unit = (km / KM_PER_MILE, "mi") if DIST_MILES else (km, "km")
    return f"{value:.1f}{unit}" if value < 10 else f"{value:.0f}{unit}"


def format_dist(info: dict[str, Any], contacts: dict, start: Optional[tuple[float, float]],
                end: Optional[tuple[float, float]], budget: Optional[int] = None) -> str:
    """!dist: '📏 You ›4.2km› a1 ›?› b2 ›3.1km› Bot | 7.3km+, 1 of 3 legs unknown | 5.0km direct'.
    Each leg needs a position at both ends. Drops the leg list, then the direct line, to fit."""
    budget = MAX_REPLY_BYTES if budget is None else budget
    icon = "📏 " if USE_EMOJI else "Dist: "
    if info.get("direct") or not info.get("path_len"):
        how = "Direct route" if info.get("direct") else "Heard directly"
        if start and end:
            return f"{icon}{how}, you are {_distance(haversine_km(start, end))} away"
        return f"{icon}{how}, your position isn't known" if not start else f"{icon}{how}, bot position not set"
    nodes = info.get("path_nodes") or []
    if not nodes:
        return f"{icon}{_hops(info['path_len'])}, path not reported"
    points = [("You", start)] + [(n, repeater_position(n, contacts)) for n in nodes] + [("Bot", end)]
    legs = [haversine_km(a[1], b[1]) if a[1] and b[1] else None for a, b in zip(points, points[1:])]
    known = [km for km in legs if km is not None]
    if not known:
        return f"{icon}{_hops(len(nodes))}, no positions known along the path"
    arrow = "›" if USE_EMOJI else ">"
    chain = points[0][0] + "".join(f" {arrow}{_distance(km) if km is not None else '?'}{arrow} {p[0]}"
                                   for km, p in zip(legs, points[1:]))
    unknown = len(legs) - len(known)
    total = f"{_distance(sum(known))}" + (f"+, {unknown} of {len(legs)} legs unknown" if unknown else " total")
    straight = f"{_distance(haversine_km(start, end))} direct" if start and end else ""
    for parts in ([chain, total, straight], [chain, total], [f"{_hops(len(nodes))}: {total}", straight],
                  [f"{_hops(len(nodes))}: {total}"]):
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


def _match_names(items: list, query: str, name_of: Callable[[Any], str]) -> list:
    """Items whose name is `query` (ignoring case), else starts with it, else contains it."""
    q = " ".join(query.split()).lower()
    if not q:
        return []
    for test in (lambda n: n == q, lambda n: n.startswith(q), lambda n: q in n):
        found = [i for i in items if test(" ".join(name_of(i).split()).lower())]
        if found:
            return found
    return []


def _contact_name(c: dict) -> str:
    return (c.get("adv_name") or "").strip()


def find_contacts(contacts: dict, query: str) -> list[dict]:
    return _match_names(list(contacts.values()), query, _contact_name)


def _names_list(names: list[str], limit: int = 4) -> str:
    """'Alice, Alan, Alfie +2 more'."""
    shown = ", ".join(n[:PATH_NAME_CHARS].strip() for n in names[:limit])
    return shown + (f" +{len(names) - limit} more" if len(names) > limit else "")


async def get_bearing(query: str, contacts: dict, start: Optional[tuple[float, float]]) -> str:
    """!bearing: '🧭 Alice: 2.3km NE (48°) from you'. Measures from the sender when their
    position is known, else from the bot. A name that isn't a contact is tried as a place."""
    icon = "🧭 " if USE_EMOJI else "Bearing: "
    q = query.strip()
    if not q:
        return icon + "Use !bearing <name or place>, e.g. !bearing Alice, !bearing NR1, !bearing 52.6,1.3"
    origin, frm = (start, "you") if start else (bot_position(), "the bot")
    if not origin:
        return icon + "Your position isn't known and the bot has none set"
    matches = find_contacts(contacts, q)
    if len(matches) > 1:
        return f"{icon}{len(matches)} contacts match '{q}': {_names_list([_contact_name(c) for c in matches])}"
    if matches:
        label, pos = _contact_name(matches[0]), _gps(matches[0])
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
    return f"{icon}{label}: {_distance(km)} {_compass(deg)} ({deg:.0f}°) from {frm}"


# ---------- !who and !status: who the bot has heard ----------
HEARD_KEY_CHARS = 12            # public key prefix kept per node, as in a DM's pubkey_prefix


def heard_id(name: str) -> str:
    """Name with emoji and symbols removed, lower case, so 'Sam 🐬 Base' and 'Sam 🐟 Base'
    are one node. A name with no letters or digits keeps its symbols."""
    plain = " ".join(re.sub(r"[^\w\s]", "", name).split()).lower()
    return plain or " ".join(name.split()).lower()


class Heard:
    """Nodes the bot has heard, by a message on a listened channel, a DM or an advert.
    Keyed by heard_id(name). A node that changes its name but keeps its key replaces its
    old entry. Loaded from HEARD_FILE at startup and saved to it on exit or by !save."""

    def __init__(self):
        self.nodes: dict[str, dict[str, Any]] = {}
        self.dirty = False

    def record(self, name: str, via: str, info: Optional[dict[str, Any]] = None, key: str = "",
               repeater: bool = False, now: Optional[float] = None) -> None:
        """via is 'ch1', 'dm' or 'advert'. info is the message's rx info (hops, SNR)."""
        name = " ".join((name or "").split())
        if not name:
            return
        node_id = heard_id(name)
        entry: dict[str, Any] = {"name": name, "at": time.time() if now is None else now, "via": via}
        key = (key or self.nodes.get(node_id, {}).get("key", "")).lower()[:HEARD_KEY_CHARS]
        if key:
            entry["key"] = key
            # Same key under another name: the node was renamed, so drop the old name
            for other in [k for k, e in self.nodes.items() if k != node_id and e.get("key") == key]:
                del self.nodes[other]
        if repeater:
            entry["repeater"] = True
        info = info or {}
        if info.get("direct"):
            entry["direct"] = True
        for field in ("path_len", "snr"):
            if info.get(field) is not None:
                entry[field] = info[field]
        self.nodes[node_id] = entry
        self.dirty = True

    def _merge(self, entry: dict[str, Any]) -> None:
        """Add a saved entry, keeping the newest when two are the same node."""
        node_id = heard_id(entry["name"])
        key = entry.get("key")
        same = [k for k, e in self.nodes.items() if k == node_id or (key and e.get("key") == key)]
        if any(self.nodes[k]["at"] >= entry["at"] for k in same):
            return
        for k in same:
            del self.nodes[k]
        self.nodes[node_id] = entry

    def find(self, query: str) -> list[dict[str, Any]]:
        return _match_names(list(self.nodes.values()), query, lambda e: e["name"])

    def recent(self, hours: float, now: Optional[float] = None, repeaters: bool = False) -> list[dict[str, Any]]:
        """People (or repeaters) heard in the last `hours`, most recent first."""
        cutoff = (time.time() if now is None else now) - hours * 3600
        found = [e for e in self.nodes.values() if e["at"] >= cutoff and bool(e.get("repeater")) == repeaters]
        return sorted(found, key=lambda e: -e["at"])

    def prune(self, now: Optional[float] = None) -> None:
        cutoff = (time.time() if now is None else now) - HEARD_KEEP_DAYS * 86400
        old = [k for k, e in self.nodes.items() if e["at"] < cutoff]
        for k in old:
            del self.nodes[k]
        self.dirty = self.dirty or bool(old)

    def load(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.nodes = {}
            for e in data:
                if e.get("name") and isinstance(e.get("at"), (int, float)):
                    self._merge(e)
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, AttributeError) as ex:
            _LOGGER.warning("Can't read %s, starting with no one heard: %s", path, ex)

    def save(self, path: str) -> bool:
        """Write the list if it changed since the last save. False when the write failed."""
        if not self.dirty:
            return True
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(list(self.nodes.values()), fh)
            os.replace(tmp, path)
            self.dirty = False
            return True
        except OSError as ex:
            _LOGGER.warning("Can't save %s: %s", path, ex)
            return False


_heard = Heard()


def heard_path() -> str:
    return HEARD_FILE if os.path.isabs(HEARD_FILE) else os.path.join(SCRIPT_DIR, HEARD_FILE)


def save_heard(heard: Optional[Heard] = None, path: Optional[str] = None) -> str:
    """!save (admin): write the heard list now, before a power cut, rather than waiting for the bot to stop."""
    heard = _heard if heard is None else heard
    path = heard_path() if path is None else path
    icon = "💾 " if USE_EMOJI else ""
    n = len(heard.nodes)
    nodes = f"{n} node{'' if n == 1 else 's'}"
    if not heard.dirty:
        return f"{icon}Nothing new to save, {nodes} already in {os.path.basename(path)}"
    if not heard.save(path):
        return f"{icon}Save failed, see the log"
    return f"{icon}Saved {nodes} to {os.path.basename(path)}"


def _ago(seconds: float) -> str:
    """Short age: '40s', '5m', '3h', '2d'."""
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds // 60:.0f}m"
    if seconds < 172800:
        return f"{seconds // 3600:.0f}h"
    return f"{seconds // 86400:.0f}d"


def _via_text(via: str) -> str:
    if via == "dm":
        return "by DM"
    if via == "advert":
        return "by advert"
    return f"on {via}"


def format_who(heard: Heard, arg: str = "", now: Optional[float] = None, budget: Optional[int] = None) -> str:
    """!who: '👥 4 heard in 24h: Alice 2m, Bob 15m, Carol 3h +1 more'.
    '!who 2' looks back 2 hours, '!who rpt' lists repeaters instead of people."""
    budget = MAX_REPLY_BYTES if budget is None else budget
    now = time.time() if now is None else now
    icon = "👥 " if USE_EMOJI else ""
    words = arg.lower().split()
    repeaters = any(w in ("rpt", "rpts", "repeater", "repeaters") for w in words)
    hours = WHO_HOURS
    for w in words:
        try:
            hours = min(max(float(w.rstrip("h")), 0.1), HEARD_KEEP_DAYS * 24)
        except ValueError:
            pass
    what = "repeaters" if repeaters else "people"
    found = heard.recent(hours, now, repeaters=repeaters)
    if not found:
        return f"{icon}No {what} heard in the last {hours:g}h"
    kind = ("repeater " if len(found) == 1 else "repeaters ") if repeaters else ""
    head = f"{icon}{len(found)} {kind}heard in {hours:g}h: "
    # Full names when they fit, else names cut to PATH_NAME_CHARS, else fewer names
    for n in range(len(found), 0, -1):
        more = f" +{len(found) - n} more" if n < len(found) else ""
        for cut in (None, PATH_NAME_CHARS):
            items = [f"{e['name'][:cut].strip()} {_ago(now - e['at'])}" for e in found[:n]]
            text = head + ", ".join(items) + more
            if len(text.encode("utf-8")) <= budget:
                return text
    return head + f"+{len(found)} more"


def format_status(query: str, heard: Heard, contacts: dict, bot_pos: Optional[tuple[float, float]],
                  now: Optional[float] = None) -> str:
    """!status: '👤 Alice: heard 12m ago on ch1, 2 hops, SNR 7.5dB | 📍 4.2km NE of bot'.
    Falls back to the contact's last advert when the bot hasn't heard them itself."""
    now = time.time() if now is None else now
    icon, pin = ("👤 ", "📍 ") if USE_EMOJI else ("", "")
    q = query.strip()
    if not q:
        return icon + "Use !status <name>, e.g. !status Alice. !who lists who's been heard"
    matches = heard.find(q)
    if len(matches) > 1:
        return f"{icon}{len(matches)} match '{q}': {_names_list([e['name'] for e in matches])}"
    contact = None
    if matches:
        e = matches[0]
        name = e["name"]
        parts = [f"heard {_ago(now - e['at'])} ago {_via_text(e['via'])}"]
        if e.get("direct"):
            parts.append("direct route")
        elif e.get("path_len") is not None:
            parts.append(_hops(e["path_len"]))
        if e.get("snr") is not None:
            parts.append(f"SNR {e['snr']:g}dB")
        by_key = [c for c in contacts.values()
                  if e.get("key") and (c.get("public_key") or "").lower().startswith(e["key"])]
        by_name = [c for c in contacts.values() if _contact_name(c).lower() == name.lower()]
        contact = (by_key or by_name or [None])[0]
    else:
        found = find_contacts(contacts, q)
        if len(found) > 1:
            return f"{icon}{len(found)} contacts match '{q}': {_names_list([_contact_name(c) for c in found])}"
        if not found:
            return f"{icon}No one called '{q}' heard"
        contact = found[0]
        name = _contact_name(contact)
        advert = contact.get("last_advert") or 0
        # last_advert is the node's own clock, so ignore one that is far in the future
        parts = [f"last advert {_ago(now - advert)} ago" if 0 < advert <= now + 3600 else "in contacts, not heard yet"]
    text = f"{icon}{name}: " + ", ".join(parts)
    pos = _gps(contact)
    if pos and bot_pos:
        km = haversine_km(bot_pos, pos)
        where = f"{_distance(km)} {_compass(initial_bearing(bot_pos, pos))} of bot" if km >= 0.05 else "at the bot"
        text += f" | {pin}{where}"
    return text


# ---------- !freq: frequency lists ----------
FREQ_ALIASES = {"pmr446": "pmr", "amateur": "ham", "sea": "marine", "boat": "marine",
                "airband": "air", "aircraft": "air", "meshcore": "mesh", "lora": "mesh"}


def mesh_freq(self_info: Optional[dict] = None) -> str:
    """The bot radio's own settings, so others can match them: 'MeshCore here: 869.618 MHz, BW 62.5kHz, SF8, CR8'."""
    if self_info is None:
        self_info = getattr(_radio, "self_info", None) or {}
    freq = self_info.get("radio_freq")
    if not freq:
        return "MeshCore radio settings not known"
    parts = [f"{freq:g} MHz"]
    if self_info.get("radio_bw"):
        parts.append(f"BW {self_info['radio_bw']:g}kHz")
    if self_info.get("radio_sf"):
        parts.append(f"SF{self_info['radio_sf']}")
    if self_info.get("radio_cr"):
        parts.append(f"CR{self_info['radio_cr']}")
    return "MeshCore here: " + ", ".join(parts)


def format_freq(topic: str, self_info: Optional[dict] = None) -> str:
    icon = "📻 " if USE_EMOJI else ""
    t = topic.strip().lower().lstrip("!")
    t = FREQ_ALIASES.get(t, t)
    if t == "mesh":
        return icon + mesh_freq(self_info)
    if t in FREQ_LISTS:
        return icon + FREQ_LISTS[t]
    return icon + "Frequencies: !freq " + ", ".join(list(FREQ_LISTS) + ["mesh"])


# =====================================================================
# Rate limiting
# =====================================================================
class SlidingWindowLimiter:
    """Allows max_events per window_sec for each key."""

    def __init__(self, max_events: int, window_sec: float):
        self.max_events = max_events
        self.window_sec = window_sec
        self._hits: dict[str, deque] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque:
        q = self._hits[key]
        while q and now - q[0] >= self.window_sec:
            q.popleft()
        return q

    def allowed(self, key: str, now: float) -> bool:
        return len(self._prune(key, now)) < self.max_events

    def record(self, key: str, now: float) -> None:
        self._hits[key].append(now)

    def retry_after(self, key: str, now: float) -> int:
        q = self._prune(key, now)
        if len(q) < self.max_events:
            return 0
        return max(1, int(self.window_sec - (now - q[0])) + 1)

    def cleanup(self, now: float) -> None:
        for key in list(self._hits):
            if not self._prune(key, now):
                del self._hits[key]


class RateLimiter:
    """Checks user, channel and global buckets. Records a hit only if all pass."""

    def __init__(self):
        self.user = SlidingWindowLimiter(*RATE_LIMIT_PER_USER)
        self.channel = SlidingWindowLimiter(*RATE_LIMIT_PER_CHANNEL)
        self.glob = SlidingWindowLimiter(*RATE_LIMIT_GLOBAL)
        self._notified: dict[str, float] = {}

    def check(self, user_key: str, chan_key: str) -> tuple[bool, str, int]:
        now = time.monotonic()
        for name, lim, key in (("user", self.user, user_key),
                               ("channel", self.channel, chan_key),
                               ("global", self.glob, "global")):
            if not lim.allowed(key, now):
                return False, name, lim.retry_after(key, now)
        self.user.record(user_key, now)
        self.channel.record(chan_key, now)
        self.glob.record("global", now)
        return True, "", 0

    def should_notify(self, user_key: str) -> bool:
        """One 'slow down' notice per user per user-window."""
        now = time.monotonic()
        last = self._notified.get(user_key)
        if last is None or now - last >= self.user.window_sec:
            self._notified[user_key] = now
            return True
        return False

    def cleanup(self) -> None:
        now = time.monotonic()
        for lim in (self.user, self.channel, self.glob):
            lim.cleanup(now)
        for k, t in list(self._notified.items()):
            if now - t >= self.user.window_sec:
                del self._notified[k]


# =====================================================================
# HTTP helpers
# =====================================================================
def _http_get(url: str, headers: Optional[dict] = None, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "meshcore-meshpotato-bot/1.0", "Accept": accept, **(headers or {})},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


def _http_get_json(url: str, headers: Optional[dict] = None) -> Any:
    return json.loads(_http_get(url, headers).decode("utf-8"))


class TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key):
        item = self._data.get(key)
        if item and time.monotonic() - item[0] < self.ttl:
            return item[1]
        self._data.pop(key, None)
        return None

    def put(self, key, value):
        self._data[key] = (time.monotonic(), value)

    def cleanup(self) -> None:
        now = time.monotonic()
        for key, (stamp, _) in list(self._data.items()):
            if now - stamp >= self.ttl:
                self._data.pop(key, None)


# =====================================================================
# Geocoding (postcodes.io, free, UK only, no key)
# =====================================================================
FULL_POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.I)
OUTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?$", re.I)
LATLON_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

_geo_cache = TTLCache(GEOCODE_CACHE_SEC)


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
    q = query.strip() or DEFAULT_LOCATION
    key = q.lower()

    named = {k.lower(): k for k in LOCATIONS}
    if key in named:
        name = named[key]
        lat, lon = LOCATIONS[name]
        return lat, lon, name.title()

    m = LATLON_RE.match(q)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon, f"{lat:.2f},{lon:.2f}"
        raise LocationError("bad lat,lon")

    cached = _geo_cache.get(key)
    if cached:
        return cached

    base = "https://api.postcodes.io"
    try:
        if FULL_POSTCODE_RE.match(q):
            data = _http_get_json(f"{base}/postcodes/{urllib.parse.quote(q)}")
            r = data["result"]
            out = (r["latitude"], r["longitude"], r["postcode"])
        elif OUTCODE_RE.match(q):
            data = _http_get_json(f"{base}/outcodes/{urllib.parse.quote(q)}")
            r = data["result"]
            out = (r["latitude"], r["longitude"], r["outcode"])
        else:
            data = _http_get_json(f"{base}/places?q={urllib.parse.quote(q)}&limit=20")
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
    _geo_cache.put(key, out)
    return out


# =====================================================================
# Met Office DataHub (Site Specific)
# =====================================================================
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
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
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
            if self.used >= WX_DAILY_CALL_BUDGET:
                return False
            self.used += 1
            return True


_wx_cache = TTLCache(WX_CACHE_SEC)
_wx_budget = CallBudget()
_fetch_locks: dict[Any, threading.Lock] = defaultdict(threading.Lock)
_fetch_locks_guard = threading.Lock()


def _compass(deg: Optional[float]) -> str:
    if deg is None:
        return "?"
    return COMPASS[int((deg % 360) / 45 + 0.5) % 8]


def _speed_value(ms: Optional[float]) -> str:
    """Speed as a bare number in the configured unit."""
    if ms is None:
        return "?"
    return f"{ms * MPH_PER_MS:.0f}" if USE_MPH else f"{ms:.0f}"


def _speed(ms: Optional[float]) -> str:
    if ms is None:
        return "?"
    return _speed_value(ms) + ("mph" if USE_MPH else "m/s")


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def wx_available() -> bool:
    """The Met Office weather commands only run with an API key."""
    return bool(MET_OFFICE_API_KEY)


def _fetch_metoffice_sync(kind: str, lat: float, lon: float) -> dict:
    if not MET_OFFICE_API_KEY:
        raise RuntimeError("METOFFICE_API_KEY not set")
    key = (kind, round(lat, 2), round(lon, 2))
    with _fetch_locks_guard:
        lock = _fetch_locks[key]
    # One fetch per place at a time, so two quick requests share one API call
    with lock:
        cached = _wx_cache.get(key)
        if cached:
            return cached
        if not _wx_budget.take():
            raise QuotaError(f"{WX_DAILY_CALL_BUDGET} calls used today")
        params = urllib.parse.urlencode({
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "excludeParameterMetadata": "true",
            "includeLocationName": "true",
        })
        data = _http_get_json(f"{MET_OFFICE_BASE}{kind}?{params}", headers={"apikey": MET_OFFICE_API_KEY})
        _wx_cache.put(key, data)
        return data


def format_hourly(data: dict, label: str) -> str:
    props = data["features"][0]["properties"]
    series = props.get("timeSeries") or []
    now = datetime.now(TIMEZONE)
    entry = next((e for e in series if _parse_time(e["time"]) >= now - timedelta(minutes=30)),
                 series[-1] if series else None)
    if not entry:
        return f"WX {label}: no data"
    t = _parse_time(entry["time"]).astimezone(TIMEZONE)
    code = entry.get("significantWeatherCode")
    desc = WX_CODES.get(code, "?")
    temp = entry.get("screenTemperature")
    feels = entry.get("feelsLikeTemperature")
    rh = entry.get("screenRelativeHumidity")
    pop = entry.get("probOfPrecipitation")
    wind = f"{_compass(entry.get('windDirectionFrom10m'))} {_speed(entry.get('windSpeed10m'))}"
    gust = _speed_value(entry.get("windGustSpeed10m"))
    if USE_EMOJI:
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
    today = datetime.now(TIMEZONE).date()
    out = []
    for e in props.get("timeSeries") or []:
        d = _parse_time(e["time"]).astimezone(TIMEZONE).date()
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
        if USE_EMOJI:
            out.append(f"{d:%a} {WX_EMOJI.get(code, desc)} {hi_s}/{lo_s}° ☔{pop_s}%")
        else:
            out.append(f"{d:%a} {desc} {hi_s}/{lo_s}C {pop_s}%")
        if len(out) >= days:
            break
    if not out:
        return f"{label}: no forecast"
    if USE_EMOJI:
        return f"\U0001F4C5 {label}: " + " | ".join(out)
    return f"{label}: " + " | ".join(out)


def format_hours(data: dict, label: str, hours: Optional[int] = None, step: Optional[int] = None,
                 budget: Optional[int] = None) -> str:
    """Hour-by-hour outlook. Adds entries until `hours` or the byte budget is reached."""
    hours = WXH_HOURS if hours is None else hours
    step = WXH_STEP_HOURS if step is None else step
    if budget is None:
        budget = MAX_REPLY_BYTES - WXH_MENTION_RESERVE
    props = data["features"][0]["properties"]
    series = props.get("timeSeries") or []
    now = datetime.now(TIMEZONE)
    upcoming = [e for e in series if _parse_time(e["time"]) >= now - timedelta(minutes=30)]
    upcoming = upcoming[:: max(1, step)]
    head = f"\U0001F552 {label}: " if USE_EMOJI else f"{label}: "
    sep = " | "
    text = head
    count = 0
    for e in upcoming:
        if count >= hours:
            break
        t = _parse_time(e["time"]).astimezone(TIMEZONE)
        code = e.get("significantWeatherCode")
        temp = e.get("screenTemperature")
        pop = e.get("probOfPrecipitation")
        temp_s = f"{temp:.0f}" if temp is not None else "?"
        pop_s = pop if pop is not None else "?"
        if USE_EMOJI:
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
        lat, lon, label = await asyncio.to_thread(_geocode_sync, query)
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


# =====================================================================
# Weather warnings (Met Office RSS, free, no key, not counted in the call budget)
# =====================================================================
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

_warn_cache = TTLCache(WARN_CACHE_SEC)


def _warn_time(hh: str, mm: str, day: str, mon: str, now: datetime) -> Optional[datetime]:
    """The feed gives '0600 Sat 04 Nov' with no year. Pick the year that lands closest to now."""
    try:
        month = _MONTHS.index(mon.lower()[:3]) + 1
        options = [datetime(y, month, int(day), int(hh), int(mm), tzinfo=TIMEZONE)
                   for y in (now.year - 1, now.year, now.year + 1)]
    except ValueError:
        return None
    return min(options, key=lambda t: abs(t - now))


def parse_warning(title: str, description: str, now: Optional[datetime] = None) -> Optional[dict]:
    m = WARN_TITLE_RE.match(" ".join(title.split()))
    if not m:
        return None
    now = now or datetime.now(TIMEZONE)
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
    if q in WARN_REGIONS and q not in {k.lower() for k in LOCATIONS}:
        return q
    lat, lon, _ = _geocode_sync(query)
    key = ("warn-region", round(lat, 3), round(lon, 3))
    cached = _geo_cache.get(key)
    if cached:
        return cached
    data = _http_get_json(f"https://api.postcodes.io/postcodes?lon={lon:.5f}&lat={lat:.5f}&limit=1&radius=2000")
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
    _geo_cache.put(key, code)
    return code


def _fetch_warnings_sync(code: str) -> list[dict]:
    cached = _warn_cache.get(code)
    if cached is not None:
        return cached
    body = _http_get(WARN_FEED + code, accept="application/rss+xml, application/xml")
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
    budget = MAX_REPLY_BYTES if budget is None else budget
    now = now or datetime.now(TIMEZONE)
    region = WARN_REGIONS.get(code, code)
    live = [w for w in warnings if not w["end"] or w["end"] > now]
    if not live:
        return f"✅ No weather warnings for {region}" if USE_EMOJI else f"No weather warnings for {region}"
    live.sort(key=lambda w: (WARN_LEVELS.get(w["level"], 9), w["start"] or now))
    items = []
    for w in live:
        hazard = w["hazard"].capitalize()
        span = _warn_span(w["start"], w["end"], now)
        area = f" ({w['area']})" if code == "uk" and w["area"] else ""
        if USE_EMOJI:
            icons = "".join(e for word, e in WARN_HAZARD_EMOJI if word in w["hazard"].lower())
            items.append(f"{WARN_LEVEL_EMOJI.get(w['level'], '⚠️')}{icons} {hazard} {span}{area}".strip())
        else:
            items.append(f"{w['level'].capitalize()} {hazard.lower()} {span}{area}".strip())
    text = (f"⚠️ {region}: " if USE_EMOJI else f"Warnings {region}: ")
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
        now = now or datetime.now(TIMEZONE)
        key = (kind, target, code)
        if key not in self.watches and len(self.watches) >= WARN_WATCH_MAX:
            _LOGGER.info("Warning watch for %s on %s %s not started, %d already running",
                         code, kind, target, WARN_WATCH_MAX)
            return
        self.watches[key] = {"until": time.monotonic() + WARN_WATCH_HOURS * 3600,
                             "seen": _live_warn_keys(warnings, now)}
        _LOGGER.info("Watching warnings for %s on %s %s for %sh", code, kind, target, WARN_WATCH_HOURS)

    async def check(self, sender: "Sender", now: Optional[datetime] = None) -> None:
        now = now or datetime.now(TIMEZONE)
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
            prefix = "🔔 " if USE_EMOJI else "Update: "
            text = prefix + format_warnings(warnings, code, now=now,
                                            budget=MAX_REPLY_BYTES - len(prefix.encode("utf-8")))
            _LOGGER.info("Warnings changed for %s, posting to %s %s", code, kind, target)
            if kind == "chan":
                sender.channel(target, text)
            else:
                sender.dm(target, text)

    async def run(self, sender: "Sender") -> None:
        while True:
            await asyncio.sleep(WARN_WATCH_TICK_SEC)
            try:
                await self.check(sender)
            except Exception:
                _LOGGER.exception("Warning watch check failed")


_warn_watch = WarnWatcher()


# =====================================================================
# Sun and moon (worked out locally, no API calls)
# =====================================================================
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
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=TIMEZONE)
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
    return (_from_jd(transit - w).astimezone(TIMEZONE),
            _from_jd(transit + w).astimezone(TIMEZONE), "")


def _hm_length(td: timedelta) -> str:
    mins = int(td.total_seconds() // 60)
    return f"{mins // 60}h{mins % 60:02d}m"


def format_sun(lat: float, lon: float, label: str, day: Optional[date] = None) -> str:
    day = day or datetime.now(TIMEZONE).date()
    rise, set_, state = sun_times(lat, lon, day)
    head = f"{label} {day:%a %d %b}"
    if state:
        msg = "sun up all day" if state == "up" else "sun down all day"
        return f"☀️ {head}: {msg}" if USE_EMOJI else f"{head}: {msg}"
    length = _hm_length(set_ - rise)
    if USE_EMOJI:
        return f"{head} 🌅 {rise:%H:%M} 🌇 {set_:%H:%M} ☀️ {length} daylight"
    return f"{head}: sunrise {rise:%H:%M} sunset {set_:%H:%M}, {length} daylight"


async def get_sun(query: str, quiet: bool = False) -> Optional[str]:
    try:
        lat, lon, label = await asyncio.to_thread(_geocode_sync, query)
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
    now = now or datetime.now(TIMEZONE)
    idx, lit = moon_phase(now)
    emoji, name = MOON_PHASES[idx]
    full = next_moon_phase(now, 180).astimezone(TIMEZONE)
    new = next_moon_phase(now, 0).astimezone(TIMEZONE)
    if USE_EMOJI:
        return f"{emoji} {name}, {lit}% lit | 🌕 Full {full:%a %d %b} | 🌑 New {new:%a %d %b}"
    return f"Moon: {name}, {lit}% lit. Full {full:%a %d %b}, new {new:%a %d %b}"


# =====================================================================
# Aurora and space weather (AuroraWatch UK, Lancaster University, free, no key)
# Terms: non-commercial use, credit AuroraWatch UK, no more than one request per
# 3 minutes, keep their level names and colours. https://aurorawatch.lancs.ac.uk/api-info/
# =====================================================================
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

_aurora_cache = TTLCache(max(AURORA_MIN_CACHE_SEC, AURORA_CACHE_SEC))


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
    status = parse_aurora_status(_http_get(AURORA_STATUS_URL, AURORA_HEADERS, accept=accept))
    try:
        activity = parse_aurora_activity(_http_get(AURORA_ACTIVITY_URL, AURORA_HEADERS, accept=accept))
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
    parts = [f"{emoji} {level.capitalize()}: {description}" if USE_EMOJI and level in AURORA_LEVELS
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


# =====================================================================
# Air quality and pollen (Open-Meteo, CAMS Europe model, free, no key)
# Terms: non-commercial use, CC BY 4.0 so replies credit Open-Meteo,
# under 10,000 calls a day. https://open-meteo.com/en/terms
# =====================================================================
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

_air_cache = TTLCache(AIR_CACHE_SEC)


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
    limits = POLLEN_LEVELS.get(kind.lower())
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
        "forecast_days": 2, "timezone": str(TIMEZONE),
    }
    data = _http_get_json(f"{AIR_API}?{urllib.parse.urlencode(params)}")
    _air_cache.put(key, data)
    return data


def format_air(data: dict, label: str, budget: Optional[int] = None) -> str:
    """'🟢 Norwich air: Fair (EAQI 22) | PM2.5 5 PM10 9 NO2 7 O3 63 µg/m³ | Open-Meteo'."""
    cur = data.get("current") or {}
    aqi = cur.get("european_aqi")
    band = eaqi_band(aqi)
    head = (f"{EAQI_EMOJI.get(band, '❔')} {label} air: " if USE_EMOJI else f"{label} air: ") + band
    if aqi is not None:
        head += f" (EAQI {aqi:.0f})"
    levels = " ".join(f"{name} {cur[k]:.0f}" for k, name in AIR_POLLUTANTS if cur.get(k) is not None)
    unit = "µg/m³" if USE_EMOJI else "ug/m3"
    parts = [head] + ([f"{levels} {unit}"] if levels else []) + [AIR_CREDIT]
    text = " | ".join(parts)
    budget = MAX_REPLY_BYTES if budget is None else budget
    if len(text.encode("utf-8")) > budget:          # drop the pollutant detail before the index
        text = " | ".join([head, AIR_CREDIT])
    return text


def pollen_peaks(data: dict, now: Optional[datetime] = None, hours: int = 24) -> dict[str, float]:
    """Highest count of each pollen type over the next `hours`, in grains/m³."""
    now = (now or datetime.now(TIMEZONE)).replace(tzinfo=None, minute=0, second=0, microsecond=0)
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
    budget = MAX_REPLY_BYTES if budget is None else budget
    head = f"🌼 {label} pollen 24h: " if USE_EMOJI else f"{label} pollen 24h: "
    unit = " grains/m³" if USE_EMOJI else " grains/m3"
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
        lat, lon, label = await asyncio.to_thread(_geocode_sync, query)
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


# =====================================================================
# Radio conditions
# HF and VHF: N0NBH solar feed (hamqsl.com). Free, credit N0NBH, fetch no more than
# once an hour (the flux updates hourly, the rest every 3 hours). https://www.hamqsl.com/solar.html
# UHF: tropospheric refraction worked out from the Open-Meteo forecast (CC BY 4.0).
# =====================================================================
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

_hamqsl_cache = TTLCache(max(HAMQSL_MIN_CACHE_SEC, HF_CACHE_SEC))
_tropo_cache = TTLCache(TROPO_CACHE_SEC)


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
    data = parse_hamqsl(_http_get(HAMQSL_URL, accept="application/xml, text/xml"))
    _hamqsl_cache.put("hamqsl", data)
    return data


def is_daytime(lat: float, lon: float, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(TIMEZONE)
    rise, set_, state = sun_times(lat, lon, now.date())
    if state:
        return state == "up"
    return rise <= now < set_


def format_hf(data: dict, day: bool, budget: Optional[int] = None) -> str:
    """'📻 HF day: 80-40m🟡 30-20m🟢 17-15m🟡 12-10m🔴 | ☀️SFI 112 🧲K2 A19 | 🔊S1-S2 | N0NBH'.
    Plain text: '80-40m Fair | 30-20m Good | ...'."""
    budget = MAX_REPLY_BYTES if budget is None else budget
    when = "day" if day else "night"
    states = [(label, data["hf"].get((name, when)) or "?") for name, label in HF_BANDS]
    sfi, k, a = data["sfi"] or "?", data["k"] or "?", data["a"] or "?"
    noise = data.get("noise")
    if USE_EMOJI:
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
              "forecast_days": 2, "timezone": str(TIMEZONE)}
    data = _http_get_json(f"{TROPO_API}?{urllib.parse.urlencode(params)}")
    _tropo_cache.put(key, data)
    return data


def tropo_outlook(data: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """Now and the strongest refraction in the next 24 hours, or None without data."""
    now = (now or datetime.now(TIMEZONE)).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    hours = [(t, g) for t, g in tropo_gradients(data) if now <= t < now + timedelta(hours=24)]
    if not hours:
        return None
    best = min(hours, key=lambda h: h[1])
    return {"now": hours[0][1], "best": best[1], "best_at": best[0]}


def format_uhf(outlook: Optional[dict], label: str) -> str:
    """'📶 Norwich UHF tropo: ➖Normal now (-42 N/km) | 24h best ⏫Enhanced Fri 03h (-95) | Open-Meteo'."""
    head = f"📶 {label} UHF tropo: " if USE_EMOJI else f"{label} UHF tropo: "
    if not outlook:
        return f"{head}no forecast data | {AIR_CREDIT}"
    now_level, best_level = tropo_level(outlook["now"]), tropo_level(outlook["best"])

    def shown(level: str) -> str:
        return TROPO_EMOJI.get(level, "") + level if USE_EMOJI else level
    text = f"{head}{shown(now_level)} now ({outlook['now']:.0f} N/km)"
    if best_level != now_level:
        text += f" | 24h best {shown(best_level)} {outlook['best_at']:%a %Hh} ({outlook['best']:.0f})"
    else:
        text += " | next 24h similar"
    return f"{text} | {AIR_CREDIT}"


def format_vhf(data: dict, tropo: Optional[str], budget: Optional[int] = None) -> str:
    """'📡 VHF: 6m Es🔴 4m Es🔴 2m Es🟢 144MHz ES Aurora🔴 | Tropo ➖Normal | N0NBH, Open-Meteo'.
    Closed shows 🔴, open shows 🟢 and what N0NBH reports. Plain text keeps the words."""
    budget = MAX_REPLY_BYTES if budget is None else budget
    items = []
    for key, label in VHF_PHENOMENA:
        state = (data["vhf"].get(key) or "?").replace("Band ", "")
        if not USE_EMOJI:
            items.append(f"{label} {state}")
        elif state.lower() == "closed":
            items.append(f"{label}🔴")
        else:
            items.append(f"{label}🟢 {state}" if state != "?" else f"{label}❔")
    sep = " " if USE_EMOJI else " | "
    head = "📡 VHF: " if USE_EMOJI else "VHF: "
    credit = HAMQSL_CREDIT + (f", {AIR_CREDIT}" if tropo else "")
    tropo_text = f"Tropo {TROPO_EMOJI.get(tropo, '')}{tropo}" if USE_EMOJI else f"Tropo {tropo}"
    text = f"{head}{sep.join(items)} | {tropo_text} | {credit}" if tropo else ""
    if not text or len(text.encode("utf-8")) > budget:   # keep the N0NBH data, drop the tropo hint
        text = f"{head}{sep.join(items)} | {HAMQSL_CREDIT}"
    return text


async def _default_latlon() -> Optional[tuple[float, float, str]]:
    try:
        return await asyncio.to_thread(_geocode_sync, "")
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
        lat, lon, label = await asyncio.to_thread(_geocode_sync, query)
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


# =====================================================================
# Text helpers
# =====================================================================
def trim(text: str, limit: Optional[int] = None) -> str:
    """Collapse whitespace and cut to `limit` UTF-8 bytes without splitting a character."""
    limit = MAX_REPLY_BYTES if limit is None else limit
    text = " ".join(text.split())
    if len(text.encode("utf-8")) <= limit:
        return text
    out = text.encode("utf-8")[: limit - 1].decode("utf-8", "ignore")
    out = out.rstrip("️‍ ")  # drop a dangling emoji modifier
    return out + "~"


def split_sender(text: str) -> tuple[str, str]:
    """Channel text arrives as 'Sender: message'."""
    if ":" in text:
        sender, body = text.split(":", 1)
        return sender.strip(), body.strip()
    return "", text.strip()


# Commands that work with or without a leading "!", but only as the whole message
PLAIN_COMMANDS = {"ping", "test"}
# Commands that only work with a leading "!" (returned as "!name")
BANG_COMMANDS = {"help", "helptest", "helpwx", "helpradio", "helpfun", "helpconv", "helpnet", "conv", "ohm", "res", "roll", "flipacoin", "eightball", "wx", "wxh", "wxf",
                 "warn", "sun", "moon", "aurora", "aq", "pollen", "path", "dist", "hf", "vhf", "uhf",
                 "who", "status", "bearing", "freq",
                 "stats", "uptime", "mute", "unmute", "say", "save"}
COMMAND_ALIASES = {"8ball": "eightball", "flip": "flipacoin", "coin": "flipacoin", "dice": "roll",
                   "warnings": "warn", "sunrise": "sun", "sunset": "sun", "trace": "path",
                   "solar": "aurora", "air": "aq", "bands": "hf", "tropo": "uhf",
                   "convert": "conv", "units": "conv", "ohms": "ohm", "vir": "ohm", "ohmslaw": "ohm",
                   "resistor": "res", "colour": "res", "color": "res",
                   "heard": "who", "seen": "status", "lastheard": "status", "brg": "bearing",
                   "find": "bearing", "freqs": "freq", "frequency": "freq"}


def parse_command(body: str) -> tuple[str, str]:
    """Return (cmd, arg). Bang commands come back as "!roll" etc.
    Unknown commands, bang commands sent without "!", and plain commands
    with extra text ("ping me later") return ("", "")."""
    body = body.strip()
    body = re.sub(r"^@\[[^\]]*\]\s*", "", body)     # strip a leading @[mention]
    if not body:
        return "", ""
    parts = body.split(None, 1)
    raw = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    bang = raw.startswith("!")
    name = raw.lstrip("!/")
    name = COMMAND_ALIASES.get(name, name)
    if name in PLAIN_COMMANDS:
        return (name, "") if not arg else ("", "")
    if name in BANG_COMMANDS and bang:
        return "!" + name, arg
    return "", ""


async def expand_tokens(text: str) -> str:
    now = datetime.now(TIMEZONE)
    text = text.replace("{time}", now.strftime("%H:%M")).replace("{date}", now.strftime("%a %d %b"))
    modes = {"wx": "now", "wxh": "hours", "wxf": "daily"}
    for m in list(re.finditer(r"\{(wx[hf]?|warn|sun|aq|pollen|uhf)(?::([^}]*))?\}", text)):
        place = m.group(2) or ""
        if m.group(1) == "uhf":
            replacement = await get_uhf(place)
        elif m.group(1) == "warn":
            replacement = await get_warnings(place)
        elif m.group(1) == "sun":
            replacement = await get_sun(place)
        elif m.group(1) in ("aq", "pollen"):
            replacement = await get_air(place, mode=m.group(1))
        else:
            replacement = await get_weather(place, mode=modes[m.group(1)])
        text = text.replace(m.group(0), replacement or "", 1)
    if "{moon}" in text:
        text = text.replace("{moon}", format_moon())
    if "{aurora}" in text:
        text = text.replace("{aurora}", await get_aurora())
    if "{hf}" in text:
        text = text.replace("{hf}", await get_hf())
    if "{vhf}" in text:
        text = text.replace("{vhf}", await get_vhf())
    return text


# =====================================================================
# Stats and uptime
# =====================================================================
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


_stats = Stats()
_radio: Optional[MeshCore] = None   # set in main() so !stats can ask for the battery level
_tx: Optional["Sender"] = None      # set in main() so !say can queue a channel message


def _duration(seconds: float) -> str:
    mins = int(seconds // 60)
    days, mins = divmod(mins, 1440)
    hours, mins = divmod(mins, 60)
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m" if mins else f"{int(seconds)}s"


def _host_uptime() -> Optional[float]:
    try:
        with open("/proc/uptime", encoding="ascii") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def format_uptime() -> str:
    since = _stats.started_at.astimezone(TIMEZONE)
    text = f"Bot up {_duration(time.monotonic() - _stats.started)} (since {since:%a %d %b %H:%M})"
    host = _host_uptime()
    if host is not None:
        text += f" | System up {_duration(host)}"
    return ("⏱️ " if USE_EMOJI else "") + text


def format_stats(battery_mv: Optional[int] = None, budget: Optional[int] = None) -> str:
    budget = MAX_REPLY_BYTES if budget is None else budget
    total = sum(_stats.commands.values())
    tail = [f"Heard {_stats.heard}", f"Sent {_stats.sent}"]
    if _stats.send_failed:
        tail.append(f"Failed {_stats.send_failed}")
    tail.append(f"Limited {_stats.limited}")
    tail.append(f"WX API {_wx_budget.used_today()}/{WX_DAILY_CALL_BUDGET}")
    if battery_mv:
        tail.append(("🔋" if USE_EMOJI else "Batt ") + f"{battery_mv / 1000:.2f}V")
    head = ("📊 " if USE_EMOJI else "") + f"Cmds {total}"
    # Show the busiest commands, as many as fit
    for n in (3, 2, 1, 0):
        top = ", ".join(f"{c.lstrip('!')} {k}" for c, k in _stats.commands.most_common(n))
        text = " | ".join([head + (f" ({top})" if top else "")] + tail)
        if len(text.encode("utf-8")) <= budget:
            return text
    return text


async def _battery_mv() -> Optional[int]:
    if _radio is None:
        return None
    try:
        result = await asyncio.wait_for(_radio.commands.get_bat(), timeout=5)
        if result.type == EventType.ERROR:
            return None
        return int((result.payload or {}).get("level") or 0) or None
    except Exception as ex:
        _LOGGER.debug("Battery read failed: %s", ex)
        return None


# =====================================================================
# Transmit queue
# =====================================================================
class Sender:
    """Serialises radio sends and enforces MIN_TX_GAP_SEC between them."""

    def __init__(self, meshcore: MeshCore):
        self.mc = meshcore
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._last_tx = 0.0

    def channel(self, idx: int, text: str) -> None:
        self._put(("chan", idx, trim(text)))

    def dm(self, pubkey_prefix: str, text: str) -> None:
        self._put(("dm", pubkey_prefix, trim(text)))

    def _put(self, item) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            _LOGGER.warning("TX queue full, dropping: %s", item)

    async def run(self) -> None:
        while True:
            kind, target, text = await self.queue.get()
            wait = MIN_TX_GAP_SEC - (time.monotonic() - self._last_tx)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                if kind == "chan":
                    result = await self.mc.commands.send_chan_msg(target, text)
                else:
                    result = await self.mc.commands.send_msg(target, text)
                if result.type == EventType.ERROR:
                    _stats.send_failed += 1
                    _LOGGER.error("Send to %s %s failed: %s", kind, target, result.payload)
                else:
                    _stats.sent += 1
                    _LOGGER.info("TX %s %s: %s", kind, target, text)
            except Exception as ex:
                _stats.send_failed += 1
                _LOGGER.error("Send exception: %s", ex)
            self._last_tx = time.monotonic()
            self.queue.task_done()


# =====================================================================
# Scheduler
# =====================================================================
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
    grace = timedelta(seconds=SCHEDULE_GRACE_SEC)

    if "at" in entry:
        try:
            target = _parse_at(entry["at"], now.year).replace(tzinfo=TIMEZONE)
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
        now = datetime.now(TIMEZONE)
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
        await asyncio.sleep(SCHEDULE_TICK_SEC)


# =====================================================================
# Command handling
# =====================================================================
WX_COMMANDS = ["!wx", "!wxh", "!wxf"]             # need a Met Office API key
PLACE_COMMANDS = ["!warn", "!sun", "!aq", "!pollen", "!uhf"]
HELP_TOPICS = ("test", "net", "wx", "radio", "fun", "conv")


def help_text(topic: str = "") -> str:
    """!help lists the topics, !help<topic> or !help <topic> the commands in one.
    The weather commands are left out when there is no Met Office API key."""
    topic = topic.strip().lower().lstrip("!")
    topic = topic[4:] if topic.startswith("help") else topic
    if topic == "test":
        return "Mesh: ping (hops), test (SNR, RSSI, distance), !path (repeaters), !dist (leg distances). ping and test need no !"
    if topic == "wx":
        wx = "!wx now, !wxh hourly, !wxf 3-day, " if wx_available() else ""
        return f"Weather: {wx}!warn warnings, !sun, !moon, !aq air, !pollen. Add a place: !sun Cromer, !aq NR1"
    if topic == "radio":
        return "Radio: !hf HF bands, !vhf 6m/4m/2m, !uhf [place] tropo, !aurora geomagnetic. See !helpconv for dBm and Ohm's law"
    if topic == "fun":
        return "Fun: !roll [2d6+1] dice, !flip coin, !8ball <question>"
    if topic == "conv":
        return "Conversions: !conv 10 mi km, !conv 5 w dbm. !ohm two of V/A/Ω/W, e.g. !ohm 12v 2a. !res 4k7 or !res yellow violet red"
    if topic == "net":
        return "Net: !who [hours|rpt] heard lately, !status <name> last heard, !bearing <name|place>, !freq <pmr|cb|ham|hf|marine|air|mesh>"
    return "Help: !helptest (ping, path), !helpnet (who, freq), !helpwx (weather), !helpradio (bands), !helpfun, !helpconv (units)"


# Only answered in a DM from a key in ADMIN_PUBKEYS. Channel messages carry no key,
# so these are never answered in a channel.
ADMIN_COMMANDS = {"!stats", "!uptime", "!mute", "!unmute", "!say", "!save"}


def command_allowed(cmd: str, admin: bool) -> bool:
    if cmd in WX_COMMANDS and not wx_available():
        return False
    return admin or cmd not in ADMIN_COMMANDS


# ---------- Admin commands ----------
_muted_until = 0.0              # time.monotonic() when a !mute ends


def mute_remaining() -> float:
    """Seconds of mute left, 0 when not muted."""
    return max(0.0, _muted_until - time.monotonic())


def _mute_end_text(seconds: float) -> str:
    return f"{datetime.now(TIMEZONE) + timedelta(seconds=seconds):%H:%M}"


def mute(arg: str) -> str:
    """!mute <minutes> starts or replaces a mute, !mute 0 ends it, !mute alone shows the state."""
    global _muted_until
    on, off = ("🔇 ", "🔊 ") if USE_EMOJI else ("", "")
    arg = arg.strip()
    if not arg:
        left = mute_remaining()
        if not left:
            return f"{off}Not muted"
        return f"{on}Muted, {_duration(left)} left (until {_mute_end_text(left)})"
    if not arg.isdigit() or int(arg) > MUTE_MAX_MINUTES:
        return f"Use !mute <minutes> (1-{MUTE_MAX_MINUTES}), !mute 0 or !unmute to end"
    minutes = int(arg)
    if minutes == 0:
        return unmute()
    _muted_until = time.monotonic() + minutes * 60
    _LOGGER.info("Muted for %d minutes", minutes)
    return f"{on}Muted for {_duration(minutes * 60)}, until {_mute_end_text(minutes * 60)}"


def unmute() -> str:
    global _muted_until
    was_muted = mute_remaining() > 0
    _muted_until = 0.0
    if was_muted:
        _LOGGER.info("Unmuted")
    return ("🔊 " if USE_EMOJI else "") + ("Unmuted" if was_muted else "Not muted")


def say(arg: str) -> str:
    """!say <ch> <text> queues text for a channel slot. Works while muted."""
    parts = arg.split(None, 1)
    if len(parts) < 2 or not parts[0].isdigit():
        return "Use !say <ch> <text>, e.g. !say 1 Net starts 20:00"
    if _tx is None:
        return "Radio not ready"
    ch = int(parts[0])
    _tx.channel(ch, parts[1])
    _LOGGER.info("Admin !say queued for ch%s", ch)
    return ("📢 " if USE_EMOJI else "") + f"Queued for ch{ch}"
WX_MODES = {"!wx": "now", "!wxh": "hours", "!wxf": "daily"}

# ---------- Fun commands ----------
_rng = random.SystemRandom()
ROLL_RE = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$")


def roll_dice(arg: str) -> str:
    """!roll -> 1d6. Accepts 'd20', '2d6', '3d8+2', '20' (one die with 20 sides)."""
    spec = (arg.split() or ["d6"])[0].lower()
    if spec.isdigit():
        spec = f"d{spec}"
    m = ROLL_RE.match(spec)
    if not m:
        return "Use !roll, !roll d20, !roll 2d6 or !roll 3d8+2"
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    mod = int(m.group(3) or 0)
    if not 1 <= count <= ROLL_MAX_DICE or not 2 <= sides <= ROLL_MAX_SIDES:
        return f"Up to {ROLL_MAX_DICE} dice with 2 to {ROLL_MAX_SIDES} sides"
    rolls = [_rng.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + mod
    label = f"{count}d{sides}" + (f"{mod:+d}" if mod else "")
    icon = "\U0001F3B2 " if USE_EMOJI else ""
    if count == 1 and not mod:
        return f"{icon}{label}: {total}"
    detail = " + ".join(str(r) for r in rolls) + (f" {'+' if mod > 0 else '-'} {abs(mod)}" if mod else "")
    return f"{icon}{label}: {detail} = {total}"


def flip_coin() -> str:
    side = _rng.choice(["Heads", "Tails"])
    return f"\U0001FA99 {side}" if USE_EMOJI else side


def eightball(question: str) -> str:
    if not question.strip():
        return "Ask a question: !eightball Will it rain?"
    icon = "\U0001F3B1 " if USE_EMOJI else ""
    return icon + _rng.choice(EIGHTBALL_ANSWERS)


# ---------- Unit conversion ----------
def _linear(dim: str, factor: float, label: str) -> tuple:
    """A unit worth `factor` base units (m, kg, l, m/s, hPa, W, Hz)."""
    return dim, (lambda v: v * factor), (lambda b: b / factor), label


def _dbm_from_w(w: float) -> float:
    if w <= 0:
        raise ValueError("power must be above 0 W")
    return 10 * math.log10(w) + 30


# name -> (dimension, to base unit, from base unit, label)
UNITS: dict[str, tuple] = {}
for _names, _unit in [
    (("mm",), _linear("length", 0.001, "mm")),
    (("cm",), _linear("length", 0.01, "cm")),
    (("m", "metre", "metres", "meter", "meters"), _linear("length", 1, "m")),
    (("km",), _linear("length", 1000, "km")),
    (("in", "inch", "inches"), _linear("length", 0.0254, "in")),
    (("ft", "foot", "feet"), _linear("length", 0.3048, "ft")),
    (("yd", "yard", "yards"), _linear("length", 0.9144, "yd")),
    (("mi", "mile", "miles"), _linear("length", 1609.344, "mi")),
    (("nmi",), _linear("length", 1852, "nmi")),
    (("g", "gram", "grams"), _linear("mass", 0.001, "g")),
    (("kg", "kilo", "kilos"), _linear("mass", 1, "kg")),
    (("oz", "ounce", "ounces"), _linear("mass", 0.028349523125, "oz")),
    (("lb", "lbs", "pound", "pounds"), _linear("mass", 0.45359237, "lb")),
    (("st", "stone"), _linear("mass", 6.35029318, "st")),
    (("ml",), _linear("volume", 0.001, "ml")),
    (("l", "litre", "litres", "liter", "liters"), _linear("volume", 1, "l")),
    (("floz",), _linear("volume", 0.0284130625, "fl oz")),             # UK
    (("pt", "pint", "pints"), _linear("volume", 0.56826125, "pt")),     # UK
    (("gal", "gallon", "gallons"), _linear("volume", 4.54609, "gal")),  # UK
    (("usgal",), _linear("volume", 3.785411784, "US gal")),
    (("ms", "mps"), _linear("speed", 1, "m/s")),
    (("kmh", "kph"), _linear("speed", 1 / 3.6, "km/h")),
    (("mph",), _linear("speed", 0.44704, "mph")),
    (("kn", "kt", "kts", "knot", "knots"), _linear("speed", 1852 / 3600, "kn")),
    (("hpa", "mb", "mbar"), _linear("pressure", 1, "hPa")),
    (("inhg",), _linear("pressure", 33.8638866667, "inHg")),
    (("mmhg",), _linear("pressure", 1.33322387415, "mmHg")),
    (("psi",), _linear("pressure", 68.9475729318, "psi")),
    (("bar",), _linear("pressure", 1000, "bar")),
    (("mw",), _linear("power", 0.001, "mW")),
    (("w", "watt", "watts"), _linear("power", 1, "W")),
    (("kw",), _linear("power", 1000, "kW")),
    (("dbm",), ("power", lambda v: 10 ** ((v - 30) / 10), _dbm_from_w, "dBm")),
    (("hz",), _linear("freq", 1, "Hz")),
    (("khz",), _linear("freq", 1e3, "kHz")),
    (("mhz",), _linear("freq", 1e6, "MHz")),
    (("ghz",), _linear("freq", 1e9, "GHz")),
    (("c", "degc", "celsius"), ("temp", lambda v: v, lambda b: b, "°C")),
    (("f", "degf", "fahrenheit"), ("temp", lambda v: (v - 32) * 5 / 9, lambda b: b * 9 / 5 + 32, "°F")),
    (("k", "kelvin"), ("temp", lambda v: v - 273.15, lambda b: b + 273.15, "K")),
]:
    for _name in _names:
        UNITS[_name] = _unit

# What to convert to when only one unit is given
CONV_DEFAULT_TO = {
    "mm": "in", "cm": "in", "m": "ft", "km": "mi", "in": "cm", "ft": "m", "yd": "m", "mi": "km",
    "nmi": "km", "g": "oz", "kg": "lb", "oz": "g", "lb": "kg", "st": "kg", "ml": "floz", "l": "pt",
    "floz": "ml", "pt": "l", "gal": "l", "usgal": "l", "ms": "mph", "kmh": "mph", "mph": "kmh",
    "kn": "mph", "hpa": "inhg", "inhg": "hpa", "mmhg": "hpa", "psi": "bar", "bar": "psi",
    "mw": "dbm", "w": "dbm", "kw": "dbm", "dbm": "w", "c": "f", "f": "c", "k": "c",
    "hz": "m", "khz": "m", "mhz": "m", "ghz": "m",
}
CONV_WORDS = {"to", "in", "into", "as", "->", ">", "="}
CONV_RE = re.compile(r"^(-?(?:\d+\.?\d*|\.\d+))\s*(.*)$")
CONV_USAGE = "Use !conv <n> <unit> [unit], e.g. !conv 10 mi km, !conv 20 c, !conv 5 w dbm"
SPEED_OF_LIGHT = 299_792_458


def _unit_key(token: str) -> str:
    """'°C' -> 'c', 'km/h' -> 'kmh'."""
    return token.lower().replace("°", "").replace("/", "")


def _num(v: float) -> str:
    """About 4 significant figures, no exponent: 16.09, 0.3454, 1609."""
    if v == 0:
        return "0"
    digits = min(10, max(0, 3 - math.floor(math.log10(abs(v)))))
    text = f"{v:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _with_unit(v: float, label: str) -> str:
    return _num(v) + ("" if label.startswith("°") else " ") + label


def convert_units(arg: str) -> str:
    """!conv 10 mi km, !conv 10mi to km, !conv 20 c (to °F), !conv 868 mhz (wavelength)."""
    m = CONV_RE.match(arg.strip().replace(",", ""))
    if not m:
        return CONV_USAGE
    value = float(m.group(1))
    tokens = " ".join(m.group(2).lower().replace("fl oz", "floz").split()).split()
    tokens = [_unit_key(t) for t in tokens]
    if len(tokens) == 3 and tokens[1] in CONV_WORDS:
        tokens.pop(1)
    if not 1 <= len(tokens) <= 2:
        return CONV_USAGE
    src = tokens[0]
    dst = tokens[1] if len(tokens) == 2 else CONV_DEFAULT_TO.get(src, "")
    for name in (src, dst):
        if name not in UNITS:
            return f"Unknown unit '{name[:20]}'. Try !conv 10 mi km or see !helpconv"
    s_dim, s_to, _, s_label = UNITS[src]
    d_dim, _, d_from, d_label = UNITS[dst]
    try:
        base = s_to(value)
        if s_dim == "temp" and base < -273.15:
            return "That's below absolute zero"
        if s_dim == d_dim:
            result = d_from(base)
        elif {s_dim, d_dim} == {"freq", "length"}:     # frequency <-> wavelength
            if base <= 0:
                return "Frequency and wavelength must be above 0"
            result = d_from(SPEED_OF_LIGHT / base)
            if len(tokens) == 1 and result < 1:       # 868 MHz reads better as 34.54 cm
                result, d_label = result * 100, "cm"
        else:
            return f"Can't convert {s_label} to {d_label}"
    except (ValueError, OverflowError) as e:
        return f"Can't convert: {e}"
    icon = "📐 " if USE_EMOJI else ""
    return f"{icon}{_with_unit(value, s_label)} = {_with_unit(result, d_label)}"



# ---------- Ohm's law (V = I x R) and power (P = V x I) ----------
OHM_PREFIXES = {"u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6}
OHM_UNITS = {"v": "V", "volt": "V", "volts": "V", "a": "I", "amp": "I", "amps": "I",
             "ohm": "R", "ohms": "R", "ω": "R", "r": "R", "": "R",
             "w": "P", "watt": "P", "watts": "P"}
OHM_LABELS = {"V": "V", "I": "A", "R": "Ω", "P": "W"}
OHM_RE = re.compile(r"(\d+\.?\d*|\.\d+)\s*([uµmkKM]?)([a-zA-ZΩω]*)")
OHM_USAGE = "Use !ohm with two of V, A, Ω, W: !ohm 12v 2a, !ohm 5v 220r, !ohm 10w 50ohm, !ohm 4.7k 20ma"


def _ohm_term(number: str, prefix: str, unit: str) -> Optional[tuple[str, float]]:
    """('12', '', 'v') -> ('V', 12.0). 'M' is mega and 'm' milli: '1M' is 1 MΩ, '20mA' is 20 mA.
    A bare prefix is ohms ('4.7k'), a bare number is not allowed."""
    unit = unit.lower()
    if unit not in OHM_UNITS or not (unit or prefix):
        return None
    return OHM_UNITS[unit], float(number) * OHM_PREFIXES.get(prefix, 1)


def _si(value: float, label: str) -> str:
    """0.25 A -> '250 mA', 4700 Ω -> '4.7 kΩ'."""
    for prefix, scale in (("G", 1e9), ("M", 1e6), ("k", 1e3), ("", 1), ("m", 1e-3), ("µ", 1e-6)):
        if abs(value) >= scale or prefix == "µ":
            return f"{_num(value / scale)} {prefix}{label}"
    return f"{_num(value)} {label}"


def ohms_law(arg: str) -> str:
    """Any two of voltage, current, resistance and power give the other two."""
    known: dict[str, float] = {}
    text = arg.strip().replace(",", "")
    terms = OHM_RE.findall(text)
    if len(terms) != 2 or OHM_RE.sub("", text).strip():
        return OHM_USAGE
    for term in terms:
        parsed = _ohm_term(*term)
        if parsed is None:
            unit = (term[1] + term[2])[:10]
            return f"Unknown unit '{unit}'. See !helpconv" if unit else "Give each value a unit: V, A, Ω or W"
        known[parsed[0]] = parsed[1]
    if len(known) != 2:
        return "Give two different values, such as volts and amps"
    if min(known.values()) <= 0:
        return "Values must be above 0"
    v, i, r, p = (known.get(k) for k in "VIRP")
    if v is not None and i is not None:
        r, p = v / i, v * i
    elif v is not None and r is not None:
        i, p = v / r, v * v / r
    elif v is not None and p is not None:
        i, r = p / v, v * v / p
    elif i is not None and r is not None:
        v, p = i * r, i * i * r
    elif i is not None and p is not None:
        v, r = p / i, p / (i * i)
    else:
        v, i = math.sqrt(p * r), math.sqrt(p / r)
    values = {"V": v, "I": i, "R": r, "P": p}
    given = ", ".join(_si(values[k], OHM_LABELS[k]) for k in "VIRP" if k in known)
    found = ", ".join(_si(values[k], OHM_LABELS[k]) for k in "VIRP" if k not in known)
    icon, arrow = ("⚡ ", "→") if USE_EMOJI else ("", "->")
    return f"{icon}{given} {arrow} {found}"



# ---------- Resistor colour code (IEC 60062) ----------
RES_DIGITS = ["black", "brown", "red", "orange", "yellow", "green", "blue", "violet", "grey", "white"]
RES_MULT = {**{c: i for i, c in enumerate(RES_DIGITS)}, "gold": -1, "silver": -2}    # power of 10
RES_TOL = {"brown": 1, "red": 2, "orange": 0.05, "yellow": 0.02, "green": 0.5, "blue": 0.25,
           "violet": 0.1, "grey": 0.01, "gold": 5, "silver": 10}                     # ± %
RES_TEMPCO = {"black": 250, "brown": 100, "red": 50, "orange": 15, "yellow": 25, "green": 20,
              "blue": 10, "violet": 5, "grey": 1}                                    # ppm/K
RES_ALIASES = {"purple": "violet", "gray": "grey", "bk": "black", "blk": "black", "bn": "brown",
               "brn": "brown", "rd": "red", "og": "orange", "org": "orange", "ye": "yellow",
               "yel": "yellow", "gn": "green", "grn": "green", "bu": "blue", "blu": "blue",
               "vi": "violet", "vio": "violet", "gy": "grey", "gry": "grey", "wh": "white",
               "wht": "white", "gd": "gold", "gld": "gold", "sv": "silver", "sr": "silver",
               "slv": "silver"}
# Grey, gold and silver have no coloured square, so they get the nearest emoji
RES_EMOJI = {"black": "⬛", "brown": "🟫", "red": "🟥", "orange": "🟧", "yellow": "🟨", "green": "🟩",
             "blue": "🟦", "violet": "🟪", "grey": "🩶", "white": "⬜", "gold": "🥇", "silver": "🥈"}
RES_RKM_RE = re.compile(r"^(\d+)([rkmg])(\d*)$")                 # 4k7, 4r7, 470r, 1m
RES_VALUE_RE = re.compile(r"^(\d+\.?\d*|\.\d+)([kmg]?)(?:ohms?|ω|r)?$")
RES_TOL_RE = re.compile(r"^±?(\d+\.?\d*|\.\d+)%$")
RES_SCALE = {"r": 1, "": 1, "k": 1e3, "m": 1e6, "g": 1e9}
RES_USAGE = "Use !res <colours> or !res <value>, e.g. !res yellow violet red gold, !res 4k7, !res 10k 1%"


def _res_value(ohms: float) -> str:
    """4700 -> '4.7 kΩ', 0.47 -> '0.47 Ω'."""
    return f"{_num(ohms)} Ω" if ohms < 1 else _si(ohms, "Ω")


def _res_colour(token: str) -> str:
    token = token.lower()
    return RES_ALIASES.get(token, token)


def _band_names(bands: list[str]) -> str:
    return " ".join(b.title() for b in bands)


def _bands_text(bands: list[str], names: bool = True) -> str:
    """'🟨🟪🟥🥇 Yellow Violet Red Gold', or just the squares when names=False.
    Plain names when USE_EMOJI is off."""
    if not USE_EMOJI:
        return _band_names(bands)
    strip = "".join(RES_EMOJI[b] for b in bands)
    return f"{strip} {_band_names(bands)}" if names else strip


def resistor_from_colours(bands: list[str]) -> str:
    """3 bands: 2 digits and multiplier (±20%). 4: plus tolerance. 5: 3 digits. 6: plus tempco.
    Raises ValueError with the reply for a bad code."""
    if bands == ["black"]:
        return f"{_bands_text(bands)} = 0 Ω zero-ohm link"
    if bands[0] in ("gold", "silver") and bands[-1] not in ("gold", "silver"):
        bands = bands[::-1]                                     # read from the wrong end
    if not 3 <= len(bands) <= 6:
        raise ValueError("Give 3 to 6 bands, e.g. !res brown black red gold")
    digit_count = 3 if len(bands) >= 5 else 2
    digits, mult, rest = bands[:digit_count], bands[digit_count], bands[digit_count + 1:]
    if any(b not in RES_DIGITS for b in digits):
        raise ValueError(f"{_band_names(digits)}: digit bands can't be gold or silver")
    if mult not in RES_MULT:
        raise ValueError(f"{mult.title()} isn't a multiplier band")
    tol = RES_TOL.get(rest[0]) if rest else 20
    if tol is None:
        raise ValueError(f"{rest[0].title()} isn't a tolerance band")
    ohms = int("".join(str(RES_DIGITS.index(b)) for b in digits)) * 10.0 ** RES_MULT[mult]
    text = f"{_bands_text(bands)} = {_res_value(ohms)} ±{_num(tol)}%"
    if len(rest) == 2:
        if rest[1] not in RES_TEMPCO:
            raise ValueError(f"{rest[1].title()} isn't a tempco band")
        text += f" {RES_TEMPCO[rest[1]]}ppm/K"
    return text


def _colour_bands(ohms: float, digit_count: int) -> Optional[list[str]]:
    """The digit and multiplier bands for `ohms`, or None if it needs more digits than that."""
    exp = math.floor(math.log10(ohms)) - (digit_count - 1)
    digits = round(ohms / 10.0 ** exp)
    if digits >= 10 ** digit_count:                             # rounding went up a decade
        exp, digits = exp + 1, round(ohms / 10.0 ** (exp + 1))
    if not -2 <= exp <= 9 or not math.isclose(digits * 10.0 ** exp, ohms, rel_tol=1e-9):
        return None
    mult = RES_DIGITS[exp] if exp >= 0 else ("gold" if exp == -1 else "silver")
    return [RES_DIGITS[int(d)] for d in str(digits)] + [mult]


def resistor_to_colours(value: str, tol: Optional[float] = None, budget: Optional[int] = None) -> str:
    """4-band (default ±5% gold) and 5-band (default ±1% brown) codes for a value.
    The colour names are dropped from beside the emoji when both codes don't fit in `budget`.
    Raises ValueError with the reply for a bad value."""
    m = RES_RKM_RE.match(value)
    if m:
        ohms = float(f"{m.group(1)}.{m.group(3) or 0}") * RES_SCALE[m.group(2)]
    else:
        m = RES_VALUE_RE.match(value)
        if not m:
            raise ValueError(RES_USAGE)
        ohms = float(m.group(1)) * RES_SCALE[m.group(2)]
    if ohms == 0:
        return f"0 Ω: {_bands_text(['black'])} (zero-ohm link)"
    tol_colour = None
    if tol is not None:
        tol_colour = next((c for c, t in RES_TOL.items() if t == tol), None)
        if tol_colour is None:
            raise ValueError(f"No band for ±{_num(tol)}%. Try 1%, 2%, 5% or 10%")
    codes = []
    for count, default in ((2, "gold"), (3, "brown")):
        bands = _colour_bands(ohms, count)
        if bands:
            codes.append(bands + [tol_colour or default])
    if not codes:
        raise ValueError(f"{_res_value(ohms)} has no colour code (0.1 Ω to 999 GΩ, 3 figures)")

    def text(names: bool) -> str:
        label = (lambda b: "") if USE_EMOJI else (lambda b: f"{len(b)}-band ")   # the squares show the count
        parts = [f"{label(b)}{_bands_text(b, names)} ±{_num(RES_TOL[b[-1]])}%" for b in codes]
        return f"{_res_value(ohms)}: " + " | ".join(parts)

    full = text(names=True)
    budget = MAX_REPLY_BYTES if budget is None else budget
    return full if len(full.encode("utf-8")) <= budget else text(names=False)


def resistor(arg: str, budget: Optional[int] = None) -> str:
    """!res yellow violet red gold -> value, !res 4k7 [5%] -> colours."""
    tokens = re.split(r"[\s,/-]+", arg.strip().lower())
    tokens = [t for t in tokens if t]
    if not tokens:
        return RES_USAGE
    colours = [_res_colour(t) for t in tokens]
    try:
        if colours[0] in RES_MULT or colours[0] in RES_TOL:
            unknown = next((t for t, c in zip(tokens, colours) if c not in RES_MULT and c not in RES_TOL), "")
            if unknown:
                return f"Unknown colour '{unknown[:12]}'. See !helpconv"
            text = resistor_from_colours(colours)
        else:
            tol = None
            if len(tokens) > 1 and RES_TOL_RE.match(tokens[-1]):
                tol = float(RES_TOL_RE.match(tokens.pop())[1])
            text = resistor_to_colours("".join(tokens), tol, budget)
    except ValueError as e:
        return str(e)
    return text


async def run_command(cmd: str, arg: str, sender_name: str, rx_info: dict[str, Any],
                      target: Optional[tuple[str, Any]] = None) -> Optional[str]:
    """target is where the reply goes, ("chan", idx) or ("dm", pubkey prefix). !warn watches it."""
    mention = f"@[{sender_name}] " if sender_name else ""
    if cmd == "ping":
        return f"{mention}{'🏓 ' if USE_EMOJI else ''}Pong {format_hops(rx_info)}"
    contacts = getattr(_radio, "contacts", None) or {}
    dm_key = target[1] if target and target[0] == "dm" else ""
    if cmd == "test":
        # '@[Alice] 📡 RX in Norwich | 🐸 (2 hops) 📶 SNR 7.5dB 〰️ RSSI -85dBm | 📏 34km'
        dish, ruler = ("📡 ", "📏 ") if USE_EMOJI else ("", "")
        parts = [f"{dish}RX in {DEFAULT_LOCATION.strip()}" if DEFAULT_LOCATION.strip() else "Test OK",
                 format_rx_report(rx_info)]
        start, end = sender_position(contacts, name=sender_name, key_prefix=dm_key), bot_position()
        if start and end:
            parts.append(ruler + _distance(haversine_km(start, end)))
        return mention + " | ".join(parts)
    if cmd == "!path":
        return mention + format_path(rx_info, repeater_names(),
                                     budget=MAX_REPLY_BYTES - len(mention.encode("utf-8")))
    if cmd == "!dist":
        start = sender_position(contacts, name=sender_name, key_prefix=dm_key)
        return mention + format_dist(rx_info, contacts, start, bot_position(),
                                     budget=MAX_REPLY_BYTES - len(mention.encode("utf-8")))
    if cmd == "!bearing":
        start = sender_position(contacts, name=sender_name, key_prefix=dm_key)
        return mention + await get_bearing(arg, contacts, start)
    if cmd == "!status":
        return mention + format_status(arg, _heard, contacts, bot_position())
    if cmd == "!who":
        return mention + format_who(_heard, arg, budget=MAX_REPLY_BYTES - len(mention.encode("utf-8")))
    if cmd == "!freq":
        return mention + format_freq(arg)
    if cmd.startswith("!help"):
        return help_text(cmd[5:] or arg)
    if cmd == "!conv":
        return mention + convert_units(arg)
    if cmd == "!ohm":
        return mention + ohms_law(arg)
    if cmd == "!res":
        return mention + resistor(arg, budget=MAX_REPLY_BYTES - len(mention.encode("utf-8")))
    if cmd == "!roll":
        return mention + roll_dice(arg)
    if cmd == "!flipacoin":
        return mention + flip_coin()
    if cmd == "!eightball":
        return mention + eightball(arg)
    budget = MAX_REPLY_BYTES - len(mention.encode("utf-8"))
    if cmd == "!warn":
        code, warnings, text = await lookup_warnings(arg, budget=budget, quiet=True)
        if target and warnings is not None:
            _warn_watch.add(target[0], target[1], code, warnings)
        return mention + text if text else None
    if cmd == "!sun":
        text = await get_sun(arg, quiet=True)
        return mention + text if text else None
    if cmd == "!moon":
        return mention + format_moon()
    if cmd == "!aurora":
        return mention + await get_aurora()
    if cmd == "!hf":
        return mention + await get_hf(budget=budget)
    if cmd == "!vhf":
        return mention + await get_vhf(budget=budget)
    if cmd == "!uhf":
        text = await get_uhf(arg, quiet=True)
        return mention + text if text else None
    if cmd in ("!aq", "!pollen"):
        text = await get_air(arg, mode=cmd[1:], budget=budget, quiet=True)
        return mention + text if text else None
    if cmd == "!stats":
        return mention + format_stats(await _battery_mv(), budget=budget)
    if cmd == "!uptime":
        return mention + format_uptime()
    if cmd == "!mute":
        return mention + mute(arg)
    if cmd == "!unmute":
        return mention + unmute()
    if cmd == "!save":
        return mention + save_heard()
    if cmd == "!say":
        return mention + say(arg)
    if cmd in WX_MODES:
        text = await get_weather(arg, mode=WX_MODES[cmd], budget=budget, quiet=True)
        return mention + text if text else None
    return None


# =====================================================================
# Main
# =====================================================================
async def main(port: str) -> None:
    global _radio, _tx
    if not MET_OFFICE_API_KEY:
        _LOGGER.warning("Met Office API key not found (env METOFFICE_API_KEY, metoffice_api_key "
                        "in config.toml, ~/.config/meshcore/metoffice_key or metoffice_key.txt). "
                        "!wx/!wxh/!wxf are ignored and left out of !help, and {wx} tokens "
                        "in scheduled messages are left blank.")
    else:
        _LOGGER.info("Met Office API key loaded from %s (%d chars)",
                     MET_OFFICE_KEY_SOURCE, len(MET_OFFICE_API_KEY))

    meshcore = await MeshCore.create_serial(port, BAUDRATE, debug=False, auto_reconnect=True)
    if meshcore is None:
        raise SystemExit(f"Could not connect on {port}")
    _LOGGER.info("Connected on %s", port)
    _radio = meshcore

    self_name = (meshcore.self_info or {}).get("name", "")
    limiter = RateLimiter()
    sender = Sender(meshcore)
    _tx = sender
    schedule = validate_schedule(SCHEDULED_MESSAGES)
    admin_prefixes = {k.strip().lower()[:12] for k in ADMIN_PUBKEYS}
    running: set[asyncio.Task] = set()      # keeps command tasks alive until they finish

    await meshcore.start_auto_message_fetching()

    async def refresh_contacts():
        """Contacts give !path its repeater names. Only re-read when the radio says they changed."""
        try:
            await asyncio.wait_for(meshcore.ensure_contacts(follow=True), timeout=30)
        except Exception as ex:
            _LOGGER.warning("Contact list read failed, !path will show hashes only: %s", ex)

    await refresh_contacts()
    _LOGGER.info("%d repeaters known for !path names", len(repeater_names()))
    _heard.load(heard_path())
    _LOGGER.info("%d nodes remembered for !who and !status", len(_heard.nodes))

    def contact_by_key(key: str) -> Optional[dict]:
        key = (key or "").lower()
        if not key:
            return None
        found = [c for c in (meshcore.contacts or {}).values()
                 if (c.get("public_key") or "").lower().startswith(key)]
        return found[0] if len(found) == 1 else None

    async def handle_advert(event):
        """An advert tells !who and !status a node is still about, repeaters included."""
        payload = event.payload or {}
        key = payload.get("public_key", "")
        contact = contact_by_key(key) or (payload if payload.get("adv_name") else None)
        if contact and _contact_name(contact) != self_name:
            _heard.record(_contact_name(contact), "advert", key=key,
                          repeater=contact.get("type") != CHAT_NODE_TYPE)

    async def handle_rx_log_data(event):
        parsed = parse_rx_log_data(event.payload or {})
        if parsed:
            latest_rx.clear()
            latest_rx.update(parsed, at=time.monotonic())

    async def respond(cmd, arg, sender_name, rx_info, reply, target):
        try:
            text = await run_command(cmd, arg, sender_name, rx_info, target)
        except Exception:
            _LOGGER.exception("Command %s failed", cmd)
            return
        if text:
            reply(text)

    def dispatch(cmd, arg, sender_name, user_key, chan_key, reply, target, rx_info, admin=False):
        if not cmd:
            return
        if not command_allowed(cmd, admin):
            why = "no Met Office API key" if cmd in WX_COMMANDS else "admin only"
            _LOGGER.info("Ignored %s from %s (%s)", cmd, user_key, why)
            return
        if not admin and mute_remaining() > 0:
            _LOGGER.info("Muted, ignoring %s from %s", cmd, user_key)
            return
        if not admin:
            ok, bucket, retry = limiter.check(user_key, chan_key)
            if not ok:
                _stats.limited += 1
                _LOGGER.info("Rate limited (%s) %s on %s, retry %ss", bucket, user_key, chan_key, retry)
                if RATE_LIMIT_NOTIFY and bucket == "user" and limiter.should_notify(user_key):
                    who = f"@[{sender_name}] " if sender_name else ""
                    reply(f"{who}Slow down, try again in {retry}s")
                return
        _stats.commands[cmd] += 1
        # Run in the background so a slow weather lookup doesn't hold up other messages
        task = asyncio.create_task(respond(cmd, arg, sender_name, rx_info, reply, target), name=f"cmd {cmd}")
        running.add(task)
        task.add_done_callback(running.discard)

    async def handle_channel_message(event):
        msg = event.payload or {}
        chan = msg.get("channel_idx")
        if chan not in CHANNEL_IDXS:
            return
        sender_name, body = split_sender(msg.get("text", ""))
        if self_name and sender_name == self_name:
            return
        _stats.heard += 1
        rx_info = message_rx_info(msg)
        named = [c for c in (meshcore.contacts or {}).values() if _contact_name(c) == sender_name]
        _heard.record(sender_name, f"ch{chan}", rx_info,
                      key=named[0].get("public_key", "") if len(named) == 1 else "")
        cmd, arg = parse_command(body)
        _LOGGER.log(logging.INFO if cmd else logging.DEBUG, "RX ch%s %s: %s", chan, sender_name, body)
        dispatch(cmd, arg, sender_name,
                 user_key=f"name:{sender_name.lower()}",
                 chan_key=f"ch:{chan}",
                 reply=lambda t: sender.channel(chan, t),
                 target=("chan", chan),
                 rx_info=rx_info)

    async def handle_contact_message(event):
        msg = event.payload or {}
        prefix = msg.get("pubkey_prefix", "")
        if not prefix:
            return
        text = msg.get("text", "")
        _stats.heard += 1
        rx_info = message_rx_info(msg)
        contact = contact_by_key(prefix)
        if contact:
            _heard.record(_contact_name(contact), "dm", rx_info, key=prefix)
        cmd, arg = parse_command(text)
        _LOGGER.log(logging.INFO if cmd else logging.DEBUG, "RX dm %s: %s", prefix, text)
        dispatch(cmd, arg, "",
                 user_key=prefix,
                 chan_key="dm",
                 reply=lambda t: sender.dm(prefix, t),
                 target=("dm", prefix),
                 rx_info=rx_info,
                 admin=prefix.lower() in admin_prefixes)

    subs = [
        meshcore.subscribe(EventType.CHANNEL_MSG_RECV, handle_channel_message),
        meshcore.subscribe(EventType.RX_LOG_DATA, handle_rx_log_data),
    ]
    if ANSWER_DMS:
        subs.append(meshcore.subscribe(EventType.CONTACT_MSG_RECV, handle_contact_message))
    for name in ("ADVERTISEMENT", "NEW_CONTACT"):
        if hasattr(EventType, name):
            subs.append(meshcore.subscribe(getattr(EventType, name), handle_advert))

    async def housekeeping():
        while True:
            await asyncio.sleep(300)
            limiter.cleanup()
            _wx_cache.cleanup()
            _geo_cache.cleanup()
            _warn_cache.cleanup()
            _air_cache.cleanup()
            _hamqsl_cache.cleanup()
            _tropo_cache.cleanup()
            _heard.prune()      # in memory only: HEARD_FILE is written on exit or by !save
            await refresh_contacts()

    tasks = [
        asyncio.create_task(sender.run(), name="sender"),
        asyncio.create_task(scheduler(sender, schedule), name="scheduler"),
        asyncio.create_task(housekeeping(), name="housekeeping"),
        asyncio.create_task(_warn_watch.run(sender), name="warn-watch"),
    ]

    _LOGGER.info("Listening on channels %s%s", CHANNEL_IDXS, " and DMs" if ANSWER_DMS else "")
    try:
        await asyncio.gather(*tasks)
    finally:
        # Only written here and by an admin !save, to spare the SD card. systemd stops the bot
        # with SIGINT, so this runs on stop, restart and a clean reboot. A power cut loses
        # what was heard since the last save.
        _heard.save(heard_path())
        for t in tasks + list(running):
            t.cancel()
        for s in subs:
            meshcore.unsubscribe(s)
        await meshcore.stop_auto_message_fetching()
        await meshcore.disconnect()
        _LOGGER.info("Disconnected")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MeshCore MeshPotato bot")
    ap.add_argument("--port", help=f"serial port, e.g. COM4 or /dev/ttyUSB0 (default {SERIAL_PORT})")
    ap.add_argument("--config", metavar="FILE", help="settings file (default config.toml next to this script)")
    ap.add_argument("--wx", metavar="LOCATION", help="print a !wx reply and exit (no radio)")
    ap.add_argument("--wxh", metavar="LOCATION", help="print a !wxh reply and exit (no radio)")
    ap.add_argument("--wxf", metavar="LOCATION", help="print a !wxf reply and exit (no radio)")
    ap.add_argument("--warn", metavar="LOCATION", nargs="?", const="",
                    help="print a !warn reply and exit (no radio)")
    ap.add_argument("--sun", metavar="LOCATION", nargs="?", const="", help="print a !sun reply and exit (no radio)")
    ap.add_argument("--moon", action="store_true", help="print a !moon reply and exit (no radio)")
    ap.add_argument("--aurora", action="store_true", help="print an !aurora reply and exit (no radio)")
    ap.add_argument("--aq", metavar="LOCATION", nargs="?", const="", help="print an !aq reply and exit (no radio)")
    ap.add_argument("--pollen", metavar="LOCATION", nargs="?", const="",
                    help="print a !pollen reply and exit (no radio)")
    ap.add_argument("--hf", action="store_true", help="print an !hf reply and exit (no radio)")
    ap.add_argument("--vhf", action="store_true", help="print a !vhf reply and exit (no radio)")
    ap.add_argument("--uhf", metavar="LOCATION", nargs="?", const="", help="print a !uhf reply and exit (no radio)")
    args = ap.parse_args()

    loaded = load_config(args.config)
    logging.getLogger().setLevel(LOG_LEVEL)
    if loaded:
        _LOGGER.info("Settings loaded from %s", loaded)
    try:
        if not wx_available() and (args.wx, args.wxh, args.wxf) != (None, None, None):
            raise SystemExit("No Met Office API key set, see section 3.5 of README.md")
        if args.wx is not None:
            print(trim(asyncio.run(get_weather(args.wx)) or ""))
        elif args.wxh is not None:
            print(trim(asyncio.run(get_weather(args.wxh, mode="hours")) or ""))
        elif args.wxf is not None:
            print(trim(asyncio.run(get_weather(args.wxf, mode="daily")) or ""))
        elif args.warn is not None:
            print(trim(asyncio.run(get_warnings(args.warn)) or ""))
        elif args.sun is not None:
            print(trim(asyncio.run(get_sun(args.sun)) or ""))
        elif args.moon:
            print(trim(format_moon()))
        elif args.aurora:
            print(trim(asyncio.run(get_aurora())))
        elif args.aq is not None:
            print(trim(asyncio.run(get_air(args.aq, mode="aq")) or ""))
        elif args.pollen is not None:
            print(trim(asyncio.run(get_air(args.pollen, mode="pollen")) or ""))
        elif args.hf:
            print(trim(asyncio.run(get_hf())))
        elif args.vhf:
            print(trim(asyncio.run(get_vhf())))
        elif args.uhf is not None:
            print(trim(asyncio.run(get_uhf(args.uhf)) or ""))
        else:
            asyncio.run(main(args.port or SERIAL_PORT))
    except KeyboardInterrupt:
        print("Stopped")
