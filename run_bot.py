"""
MeshPotato bot (serial companion radio) for use on MeshCore

Built on the patterns in meshcore_py/examples/serial_pingbot.py and meshcore_py/examples/serial_rss_bot.py.

Commands (channel or direct message):
  ping               -> Pong with hop count         (whole message, a leading ! is optional)
  test               -> Test OK with hops, path, SNR and RSSI (same rules as ping)
  !wx [location]     -> Current conditions from Met Office DataHub (hourly)
  !wxh [location]    -> Next few hours, hour by hour (hourly)
  !wxf [location]    -> 3-day forecast from Met Office DataHub (daily)
  !help              -> Command list                       (the ! is required)
  !roll [NdS+M]      -> Roll dice: !roll, !roll d20, !roll 2d6+3
  !flipacoin         -> Heads or tails
  !eightball <q>     -> Ask the eight ball a yes/no question

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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
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
WXH_HOURS = 6                   # wxh: max hours to show (cut to fit MAX_REPLY_BYTES)
WXH_STEP_HOURS = 1              # wxh: 1 = every hour, 2 = every 2 hours, 3 = every 3 hours
WXH_MENTION_RESERVE = 25        # wxh: bytes kept free for text around {wxh} in schedules

# ---------- Rate limits: (max_events, window_seconds) ----------
RATE_LIMIT_GLOBAL = (20, 60)        # all commands across the bot
RATE_LIMIT_PER_USER = (3, 60)       # per pubkey (DM) or per sender name (channel)
RATE_LIMIT_PER_CHANNEL = (8, 60)    # per channel index ("dm" bucket for DMs)
RATE_LIMIT_NOTIFY = True            # tell a user once per window when they hit a limit
MIN_TX_GAP_SEC = 3.0                # minimum seconds between any two radio sends
ADMIN_PUBKEYS: set[str] = set()     # pubkey prefixes (12 hex chars) exempt from limits, DMs only

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

# ---------- Scheduled messages ----------
# Each entry needs "text" and a target: "channel": <idx> or "dm": "<pubkey prefix>".
# Timing, pick one:
#   "time": "HH:MM"                 daily, local TIMEZONE
#       optional "days": ["mon", "tue", ...]
#   "at": "YYYY-MM-DD HH:MM"        one-shot local timestamp
#   "every_minutes": N              repeating interval, optional "start": "HH:MM"
# Text tokens: {time} {date} {wx} {wx:place} {wxh} {wxh:place} {wxf} {wxf:place}
SCHEDULED_MESSAGES: list[dict[str, Any]] = [
    {"name": "morning-wx", "time": "07:30", "days": ["mon", "tue", "wed", "thu", "fri"],
     "channel": 1, "text": "Morning WX {wx}"},
    {"name": "weekend-fcst", "time": "08:30", "days": ["sat", "sun"],
     "channel": 1, "text": "{wxf}"},
    {"name": "xmas", "at": "2026-12-25 09:00",
     "channel": 1, "text": "Merry Christmas."},
]
SCHEDULE_GRACE_SEC = 120        # fire a slot up to this long after its due time
SCHEDULE_TICK_SEC = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_LOGGER = logging.getLogger("meshpotato_bot")


def _load_api_key() -> str:
    """Key lookup order:
    1. METOFFICE_API_KEY environment variable
    2. file named by METOFFICE_KEY_FILE
    3. ~/.config/meshcore/metoffice_key
    4. metoffice_key.txt next to this script
    Quotes, spaces and Windows line endings are stripped.
    """
    def clean(v: str) -> str:
        return v.strip().strip('"').strip("'").strip()

    key = clean(os.environ.get("METOFFICE_API_KEY", ""))
    if key:
        return key
    candidates = [
        os.environ.get("METOFFICE_KEY_FILE", ""),
        os.path.expanduser("~/.config/meshcore/metoffice_key"),
        os.path.join(SCRIPT_DIR, "metoffice_key.txt"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    key = clean(fh.read())
                if key:
                    return key
            except OSError:
                pass
    return ""


MET_OFFICE_API_KEY = _load_api_key()
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
    "WXH_HOURS": None,
    "WXH_STEP_HOURS": None,
    "WXH_MENTION_RESERVE": None,
    "RATE_LIMIT_GLOBAL": _pair,
    "RATE_LIMIT_PER_USER": _pair,
    "RATE_LIMIT_PER_CHANNEL": _pair,
    "RATE_LIMIT_NOTIFY": None,
    "MIN_TX_GAP_SEC": float,
    "ADMIN_PUBKEYS": lambda v: {str(k) for k in v},
    "ROLL_MAX_DICE": None,
    "ROLL_MAX_SIDES": None,
    "EIGHTBALL_ANSWERS": lambda v: [str(a) for a in v],
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

    _wx_cache.ttl = WX_CACHE_SEC
    _geo_cache.ttl = GEOCODE_CACHE_SEC
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
    """test: '(2 hops, a1:b2) SNR 7.5dB RSSI -85dBm'."""
    n = info.get("path_len")
    if info.get("direct"):
        path = "(direct route)"
    elif n is None:
        path = "(? hops, ?)"
    elif n == 0:
        path = "(0 hops, direct)"
    else:
        nodes = info.get("path_nodes") or []
        path = f"({_hops(n)}, {':'.join(nodes) if nodes else '?'})"
    parts = [path]
    if info.get("snr") is not None:
        parts.append(f"SNR {info['snr']:g}dB")
    if info.get("rssi") is not None:
        parts.append(f"RSSI {info['rssi']}dBm")
    return " ".join(parts)


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
def _http_get_json(url: str, headers: Optional[dict] = None) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "meshcore-meshpotato-bot/1.0", "Accept": "application/json", **(headers or {})},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
            data = _http_get_json(f"{base}/places?q={urllib.parse.quote(q)}&limit=1")
            res = data.get("result") or []
            if not res:
                raise LocationError(f"'{q}' not found")
            r = res[0]
            out = (r["latitude"], r["longitude"], r.get("name_1") or q.title())
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
    quiet=True returns None instead of an error reply when the place isn't found."""
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
BANG_COMMANDS = {"help", "roll", "flipacoin", "eightball", "wx", "wxh", "wxf"}
COMMAND_ALIASES = {"8ball": "eightball", "flip": "flipacoin", "coin": "flipacoin", "dice": "roll"}


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
    for m in list(re.finditer(r"\{(wx[hf]?)(?::([^}]*))?\}", text)):
        replacement = await get_weather(m.group(2) or "", mode=modes[m.group(1)]) or ""
        text = text.replace(m.group(0), replacement, 1)
    return text


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
                    _LOGGER.error("Send to %s %s failed: %s", kind, target, result.payload)
                else:
                    _LOGGER.info("TX %s %s: %s", kind, target, text)
            except Exception as ex:
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


def due_slot(entry: dict, now: datetime) -> Optional[str]:
    """Return a unique slot id if the entry is due now, else None."""
    grace = timedelta(seconds=SCHEDULE_GRACE_SEC)

    if "at" in entry:
        target = datetime.strptime(entry["at"], "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
        return f"at:{entry['at']}" if target <= now < target + grace else None

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
                datetime.strptime(e["at"], "%Y-%m-%d %H:%M")
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
            _LOGGER.info("Schedule '%s' firing (%s)", entry.get("name", i), slot)
            text = await expand_tokens(entry["text"])
            if "channel" in entry:
                sender.channel(int(entry["channel"]), text)
            else:
                sender.dm(entry["dm"], text)
        await asyncio.sleep(SCHEDULE_TICK_SEC)


# =====================================================================
# Command handling
# =====================================================================
HELP_TEXT = ("Cmds: ping, test, !wx/!wxh/!wxf [place] (now/hours/3 days), "
             "!roll [2d6], !flipacoin, !eightball <question>, !help")
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


async def run_command(cmd: str, arg: str, sender_name: str, rx_info: dict[str, Any]) -> Optional[str]:
    mention = f"@[{sender_name}] " if sender_name else ""
    if cmd == "ping":
        return f"{mention}Pong {format_hops(rx_info)}"
    if cmd == "test":
        return f"{mention}Test OK {format_rx_report(rx_info)}"
    if cmd == "!help":
        return HELP_TEXT
    if cmd == "!roll":
        return mention + roll_dice(arg)
    if cmd == "!flipacoin":
        return mention + flip_coin()
    if cmd == "!eightball":
        return mention + eightball(arg)
    if cmd in WX_MODES:
        budget = MAX_REPLY_BYTES - len(mention.encode("utf-8"))
        text = await get_weather(arg, mode=WX_MODES[cmd], budget=budget, quiet=True)
        return mention + text if text else None
    return None


# =====================================================================
# Main
# =====================================================================
async def main(port: str) -> None:
    if not MET_OFFICE_API_KEY:
        _LOGGER.warning("Met Office API key not found (env METOFFICE_API_KEY, "
                        "~/.config/meshcore/metoffice_key or metoffice_key.txt). "
                        "!wx/!wxh/!wxf will reply with an error.")
    else:
        _LOGGER.info("Met Office API key loaded (%d chars)", len(MET_OFFICE_API_KEY))

    meshcore = await MeshCore.create_serial(port, BAUDRATE, debug=False, auto_reconnect=True)
    if meshcore is None:
        raise SystemExit(f"Could not connect on {port}")
    _LOGGER.info("Connected on %s", port)

    self_name = (meshcore.self_info or {}).get("name", "")
    limiter = RateLimiter()
    sender = Sender(meshcore)
    schedule = validate_schedule(SCHEDULED_MESSAGES)
    admin_prefixes = {k.strip().lower()[:12] for k in ADMIN_PUBKEYS}
    running: set[asyncio.Task] = set()      # keeps command tasks alive until they finish

    await meshcore.start_auto_message_fetching()

    async def handle_rx_log_data(event):
        parsed = parse_rx_log_data(event.payload or {})
        if parsed:
            latest_rx.clear()
            latest_rx.update(parsed, at=time.monotonic())

    async def respond(cmd, arg, sender_name, rx_info, reply):
        try:
            text = await run_command(cmd, arg, sender_name, rx_info)
        except Exception:
            _LOGGER.exception("Command %s failed", cmd)
            return
        if text:
            reply(text)

    def dispatch(cmd, arg, sender_name, user_key, chan_key, reply, rx_info, admin=False):
        if not cmd:
            return
        if not admin:
            ok, bucket, retry = limiter.check(user_key, chan_key)
            if not ok:
                _LOGGER.info("Rate limited (%s) %s on %s, retry %ss", bucket, user_key, chan_key, retry)
                if RATE_LIMIT_NOTIFY and bucket == "user" and limiter.should_notify(user_key):
                    who = f"@[{sender_name}] " if sender_name else ""
                    reply(f"{who}Slow down, try again in {retry}s")
                return
        # Run in the background so a slow weather lookup doesn't hold up other messages
        task = asyncio.create_task(respond(cmd, arg, sender_name, rx_info, reply), name=f"cmd {cmd}")
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
        cmd, arg = parse_command(body)
        _LOGGER.log(logging.INFO if cmd else logging.DEBUG, "RX ch%s %s: %s", chan, sender_name, body)
        dispatch(cmd, arg, sender_name,
                 user_key=f"name:{sender_name.lower()}",
                 chan_key=f"ch:{chan}",
                 reply=lambda t: sender.channel(chan, t),
                 rx_info=message_rx_info(msg))

    async def handle_contact_message(event):
        msg = event.payload or {}
        prefix = msg.get("pubkey_prefix", "")
        if not prefix:
            return
        text = msg.get("text", "")
        cmd, arg = parse_command(text)
        _LOGGER.log(logging.INFO if cmd else logging.DEBUG, "RX dm %s: %s", prefix, text)
        dispatch(cmd, arg, "",
                 user_key=prefix,
                 chan_key="dm",
                 reply=lambda t: sender.dm(prefix, t),
                 rx_info=message_rx_info(msg),
                 admin=prefix.lower() in admin_prefixes)

    subs = [
        meshcore.subscribe(EventType.CHANNEL_MSG_RECV, handle_channel_message),
        meshcore.subscribe(EventType.RX_LOG_DATA, handle_rx_log_data),
    ]
    if ANSWER_DMS:
        subs.append(meshcore.subscribe(EventType.CONTACT_MSG_RECV, handle_contact_message))

    async def housekeeping():
        while True:
            await asyncio.sleep(300)
            limiter.cleanup()
            _wx_cache.cleanup()
            _geo_cache.cleanup()

    tasks = [
        asyncio.create_task(sender.run(), name="sender"),
        asyncio.create_task(scheduler(sender, schedule), name="scheduler"),
        asyncio.create_task(housekeeping(), name="housekeeping"),
    ]

    _LOGGER.info("Listening on channels %s%s", CHANNEL_IDXS, " and DMs" if ANSWER_DMS else "")
    try:
        await asyncio.gather(*tasks)
    finally:
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
    args = ap.parse_args()

    loaded = load_config(args.config)
    logging.getLogger().setLevel(LOG_LEVEL)
    if loaded:
        _LOGGER.info("Settings loaded from %s", loaded)
    try:
        if args.wx is not None:
            print(trim(asyncio.run(get_weather(args.wx)) or ""))
        elif args.wxh is not None:
            print(trim(asyncio.run(get_weather(args.wxh, mode="hours")) or ""))
        elif args.wxf is not None:
            print(trim(asyncio.run(get_weather(args.wxf, mode="daily")) or ""))
        else:
            asyncio.run(main(args.port or SERIAL_PORT))
    except KeyboardInterrupt:
        print("Stopped")
