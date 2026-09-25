"""Settings: the defaults below, and config.toml on top of them.

Override any of them in config.toml (see config.example.toml) using the same names
in lower case. Other modules read them as cfg.NAME, so a change is seen everywhere.
"""

import logging
import os
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

try:
    import tomllib                  # Python 3.11+
except ModuleNotFoundError:         # Python 3.10: config.toml is not supported
    tomllib = None

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))    # the folder run_bot.py is in

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

# ---------- Mailbox: !mail <name> <message> ----------
# Held in memory and passed on by DM the next time the bot hears the recipient's key (advert or DM).
MAIL_MAX_PER_PAIR = 10          # most messages waiting from one sender to one recipient
MAIL_MAX_PER_SENDER = 30        # most messages waiting from one sender to everyone
MAIL_MAX_TOTAL = 200            # most messages waiting in all
MAIL_KEEP_DAYS = 7              # drop a message not delivered in this many days
MAIL_MAX_BYTES = 100            # longest message, so it fits in one DM with the sender and age
MAIL_FILE = "mail.json"         # saved on stop and by !save. Relative = next to run_bot.py
MAIL_USERS_FILE = "authed_mail_users.json"      # keys allowed to use !mail, set with !addmailuser.
                                                # Written when an admin changes it. Relative = next to run_bot.py
MAIL_TX_RESERVE = 5             # send-queue slots kept free for replies when passing on mail

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
    5. metoffice_key.txt next to run_bot.py
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
    "MAIL_MAX_PER_PAIR": None,
    "MAIL_MAX_PER_SENDER": None,
    "MAIL_MAX_TOTAL": None,
    "MAIL_KEEP_DAYS": float,
    "MAIL_MAX_BYTES": None,
    "MAIL_FILE": None,
    "MAIL_USERS_FILE": None,
    "FREQ_LISTS": lambda d: {k: t for k, t in {**FREQ_LISTS, **{str(k).lower(): str(v) for k, v in d.items()}}.items()
                             if t},
    "SCHEDULED_MESSAGES": lambda v: [dict(e) for e in v],
    "SCHEDULE_GRACE_SEC": None,
    "SCHEDULE_TICK_SEC": None,
}


def load_config(path: Optional[str] = None) -> Optional[str]:
    """Override the settings above from a TOML file.
    Uses `path`, else $MESHPOTATO_CONFIG, else config.toml next to run_bot.py.
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
    return path
