import asyncio
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import run_bot as bot


# ---------- parse_command ----------
@pytest.mark.parametrize("body, expected", [
    ("ping", ("ping", "")),
    ("PING", ("ping", "")),
    ("!ping", ("ping", "")),
    ("  test  ", ("test", "")),
    ("@[Bot] ping", ("ping", "")),
    ("ping me later", ("", "")),
    ("Test of new antenna", ("", "")),
    ("!ping now", ("", "")),
    ("wx", ("", "")),
    ("wx looks bad today", ("", "")),
    ("!wx", ("!wx", "")),
    ("!wx NR1 3JU", ("!wx", "NR1 3JU")),
    ("!WXH cromer", ("!wxh", "cromer")),
    ("!wxf", ("!wxf", "")),
    ("help", ("", "")),
    ("!help", ("!help", "")),
    ("!dice 2d6", ("!roll", "2d6")),
    ("!8ball will it rain", ("!eightball", "will it rain")),
    ("!coin", ("!flipacoin", "")),
    ("!warn", ("!warn", "")),
    ("!warnings Cromer", ("!warn", "Cromer")),
    ("!sunset NR1", ("!sun", "NR1")),
    ("!moon", ("!moon", "")),
    ("moon", ("", "")),
    ("!stats", ("!stats", "")),
    ("!uptime", ("!uptime", "")),
    ("!mute 30", ("!mute", "30")),
    ("!say 1 Net starts 20:00", ("!say", "1 Net starts 20:00")),
    ("!nothing", ("", "")),
    ("", ("", "")),
])
def test_parse_command(body, expected):
    assert bot.parse_command(body) == expected


def test_split_sender():
    assert bot.split_sender("Alice: !wx Cromer") == ("Alice", "!wx Cromer")
    assert bot.split_sender("no sender") == ("", "no sender")


# ---------- trim ----------
def test_trim_short_text_unchanged():
    assert bot.trim("hello   world") == "hello world"


def test_trim_cuts_to_bytes_without_splitting_emoji():
    text = "☀️" * 50
    out = bot.trim(text, limit=20)
    assert len(out.encode("utf-8")) <= 20
    assert out.endswith("~")
    out.encode("utf-8")  # still valid UTF-8


# ---------- dice, coin, eight ball ----------
@pytest.mark.parametrize("arg, dice, sides", [("", 1, 6), ("d20", 1, 20), ("20", 1, 20), ("3d8", 3, 8)])
def test_roll_dice_in_range(arg, dice, sides):
    out = bot.roll_dice(arg)
    total = int(out.rsplit(" ", 1)[1])
    assert dice <= total <= dice * sides


def test_roll_dice_modifier():
    out = bot.roll_dice("2d6+3")
    assert "2d6+3" in out
    assert 5 <= int(out.rsplit(" ", 1)[1]) <= 15


@pytest.mark.parametrize("arg", ["11d6", "d1", "d1001", "banana"])
def test_roll_dice_rejects_bad_specs(arg):
    assert "=" not in bot.roll_dice(arg)
    assert not bot.roll_dice(arg).startswith("\U0001F3B2")


def test_eightball_needs_question():
    assert bot.eightball("").startswith("Ask a question")
    reply = bot.eightball("Will it rain?")
    assert any(a in reply for a in bot.EIGHTBALL_ANSWERS)


# ---------- path and signal info ----------
def test_parse_rx_log_uses_meshcore_fields():
    parsed = bot.parse_rx_log_data({"path_len": 2, "path": "a1b2", "path_hash_size": 1, "snr": 7.5, "rssi": -85})
    assert parsed == {"path_len": 2, "path_nodes": ["a1", "b2"], "snr": 7.5, "rssi": -85}


def test_parse_rx_log_multibyte_hashes():
    parsed = bot.parse_rx_log_data({"path_len": 2, "path": "a1b2c3d4", "path_hash_size": 2})
    assert parsed["path_nodes"] == ["a1b2", "c3d4"]


def test_parse_raw_packet_flood():
    # header 0x11 (route type 1 = flood), path byte 0x02, path a1 b2, then payload
    parsed = bot.parse_rx_log_data({"payload": "1102a1b2ffff"})
    assert parsed == {"path_len": 2, "path_nodes": ["a1", "b2"]}


def test_parse_raw_packet_skips_transport_codes():
    # header 0x10 (route type 0 = transport flood), 4 transport bytes, path byte 0x01, path c3
    parsed = bot.parse_rx_log_data({"payload": "100000000001c3ff"})
    assert parsed == {"path_len": 1, "path_nodes": ["c3"]}


def test_parse_raw_packet_truncated():
    assert bot.parse_rx_log_data({"payload": "1105a1"}) == {}


def _set_latest_rx(**fields):
    bot.latest_rx.clear()
    bot.latest_rx.update(fields, at=time.monotonic())


def test_message_rx_info_uses_matching_rx_log():
    _set_latest_rx(path_len=2, path_nodes=["a1", "b2"], snr=6.0, rssi=-90)
    info = bot.message_rx_info({"path_len": 2, "SNR": 7.25})
    assert info == {"path_len": 2, "path_nodes": ["a1", "b2"], "snr": 7.25, "rssi": -90}


def test_message_rx_info_ignores_rx_log_for_other_packet():
    _set_latest_rx(path_len=5, path_nodes=["aa"] * 5, snr=1.0, rssi=-120)
    info = bot.message_rx_info({"path_len": 1})
    assert info == {"path_len": 1, "snr": None, "rssi": None}


def test_message_rx_info_ignores_stale_rx_log():
    bot.latest_rx.clear()
    bot.latest_rx.update(path_len=1, rssi=-80, at=time.monotonic() - 60)
    assert bot.message_rx_info({"path_len": 1})["rssi"] is None


@pytest.mark.parametrize("msg", [{"path_len": 255}, {"path_len": 63, "path_hash_mode": 3}])
def test_message_rx_info_direct_route(msg):
    bot.latest_rx.clear()
    info = bot.message_rx_info(msg)
    assert info["direct"] is True
    assert bot.format_hops(info) == "(direct route)"


def test_format_hops():
    assert bot.format_hops({"path_len": 0}) == "(0 hops)"
    assert bot.format_hops({"path_len": 1}) == "(1 hop)"
    assert bot.format_hops({"path_len": 3, "path_nodes": ["a1"]}) == "(3 hops)"
    assert bot.format_hops({}) == "(? hops)"


def test_format_rx_report():
    info = {"path_len": 2, "path_nodes": ["a1", "b2"], "snr": 7.5, "rssi": -85}
    assert bot.format_rx_report(info) == "(2 hops) SNR 7.5dB RSSI -85dBm"
    assert bot.format_rx_report({"path_len": 0, "snr": 10.0}) == "(0 hops) SNR 10dB"
    assert bot.format_rx_report({}) == "(? hops)"


CONTACTS = {
    "a1ff": {"public_key": "A1FF00", "adv_name": "Norwich Cathedral RPT", "type": 2},
    "b2aa": {"public_key": "b2aa00", "adv_name": "Hill Top", "type": 2},
    "b2bb": {"public_key": "b2bb00", "adv_name": "Other Hill", "type": 2},     # b2 is ambiguous
    "c3cc": {"public_key": "c3cc00", "adv_name": "Bob's phone", "type": 1},    # companion, not a repeater
}


def test_repeater_names():
    names = bot.repeater_names(CONTACTS)
    assert names == {"a1ff00": "Norwich Cathedral RPT", "b2aa00": "Hill Top", "b2bb00": "Other Hill"}


def test_format_path_names_unique_matches_only():
    info = {"path_len": 3, "path_nodes": ["a1", "b2", "c3"]}
    out = bot.format_path(info, bot.repeater_names(CONTACTS))
    assert out == "🛤️ 3 hops: a1 Norwich Cath › b2 › c3"
    assert bot.format_path({"path_len": 2, "path_nodes": ["b2aa", "c3cc"]},
                           bot.repeater_names(CONTACTS)) == "🛤️ 2 hops: b2aa Hill Top › c3cc"


def test_format_path_fits_budget():
    nodes = [f"{i:02x}" for i in range(20)]
    info = {"path_len": 20, "path_nodes": nodes}
    names = {f"{i:02x}0000": f"Repeater{i}" for i in range(20)}
    out = bot.format_path(info, names, budget=60)
    assert len(out.encode("utf-8")) <= 60
    assert out.startswith("🛤️ 20 hops: 00 › 01 › ") and out.endswith(" more")
    short = bot.format_path({"path_len": 2, "path_nodes": ["a1", "b2"]}, names={"a1ff": "X" * 12}, budget=30)
    assert short == "🛤️ 2 hops: a1 › b2"                # names dropped before hops are


@pytest.mark.parametrize("info, expected", [
    ({"direct": True}, "🛤️ Direct route, the path isn't carried in the message"),
    ({"path_len": 0}, "🛤️ 0 hops, heard directly"),
    ({"path_len": 2}, "🛤️ 2 hops, path not reported"),
    ({}, "🛤️ Path unknown"),
])
def test_format_path_without_nodes(info, expected):
    assert bot.format_path(info) == expected


def test_path_command_and_trace_alias(monkeypatch):
    monkeypatch.setattr(bot, "_radio", type("Radio", (), {"contacts": CONTACTS})())
    assert bot.parse_command("!trace") == ("!path", "")
    assert bot.parse_command("path") == ("", "")
    info = {"path_len": 1, "path_nodes": ["a1"]}
    assert asyncio.run(bot.run_command("!path", "", "Alice", info)) == "@[Alice] 🛤️ 1 hop: a1 Norwich Cath"
    monkeypatch.setattr(bot, "USE_EMOJI", False)
    monkeypatch.setattr(bot, "_radio", None)
    assert bot.format_path({"path_len": 2, "path_nodes": ["a1", "b2"]}, bot.repeater_names()) == "Path 2 hops: a1 > b2"


def test_help_fits_one_message():
    assert "!path" in bot.HELP_TEXT
    assert len(bot.HELP_TEXT.encode("utf-8")) <= bot.MAX_REPLY_BYTES


# ---------- commands ----------
def test_ping_shows_only_hops():
    info = {"path_len": 2, "path_nodes": ["a1", "b2"], "snr": 7.5, "rssi": -85}
    assert asyncio.run(bot.run_command("ping", "", "Alice", info)) == "@[Alice] Pong (2 hops)"


def test_test_shows_rx_report():
    info = {"path_len": 2, "path_nodes": ["a1", "b2"], "snr": 7.5, "rssi": -85}
    assert asyncio.run(bot.run_command("test", "", "", info)) == "Test OK (2 hops) SNR 7.5dB RSSI -85dBm"


def test_wx_unknown_place_is_silent(monkeypatch):
    def not_found(query):
        raise bot.LocationError(f"'{query}' not found")
    monkeypatch.setattr(bot, "_geocode_sync", not_found)
    assert asyncio.run(bot.run_command("!wx", "nowhere", "Alice", {})) is None
    # Scheduled messages and the CLI still get the error text
    assert asyncio.run(bot.get_weather("nowhere")) == "WX: 'nowhere' not found"


def test_place_lookup_prefers_bigger_exact_match(monkeypatch):
    results = [
        {"name_1": "Brighton", "local_type": "Hamlet", "latitude": 50.3, "longitude": -4.9},
        {"name_1": "New Brighton", "local_type": "Town", "latitude": 53.4, "longitude": -3.0},
        {"name_1": "Brighton", "local_type": "Other Settlement", "latitude": 50.8, "longitude": -0.1},
        {"name_1": "Casnewydd", "name_2": "Newport", "local_type": "City", "latitude": 51.6, "longitude": -3.0},
    ]
    assert bot._best_place(results, "brighton")["latitude"] == 50.8
    assert bot._best_place(results, "Nowhere")["latitude"] == 50.3        # no exact match: first result
    monkeypatch.setattr(bot, "_geo_cache", bot.TTLCache(60))
    monkeypatch.setattr(bot, "_http_get_json", lambda url, headers=None: {"result": results})
    assert bot._geocode_sync("Newport") == (51.6, -3.0, "Newport")


# ---------- rate limiting ----------
def test_rate_limiter_per_user(monkeypatch):
    monkeypatch.setattr(bot, "RATE_LIMIT_PER_USER", (2, 60))
    limiter = bot.RateLimiter()
    assert limiter.check("u1", "ch:1")[0]
    assert limiter.check("u1", "ch:1")[0]
    ok, bucket, retry = limiter.check("u1", "ch:1")
    assert not ok and bucket == "user" and 1 <= retry <= 61
    assert limiter.check("u2", "ch:1")[0]
    assert limiter.should_notify("u1")
    assert not limiter.should_notify("u1")


# ---------- Met Office call budget ----------
def test_call_budget(monkeypatch):
    monkeypatch.setattr(bot, "WX_DAILY_CALL_BUDGET", 2)
    budget = bot.CallBudget()
    assert budget.take() and budget.take()
    assert not budget.take()
    budget._day = budget._day - timedelta(days=1)     # a new UTC day resets the count
    assert budget.take()


def test_fetch_uses_cache_and_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "MET_OFFICE_API_KEY", "key")
    monkeypatch.setattr(bot, "_wx_cache", bot.TTLCache(60))
    monkeypatch.setattr(bot, "_wx_budget", bot.CallBudget())
    monkeypatch.setattr(bot, "WX_DAILY_CALL_BUDGET", 1)
    monkeypatch.setattr(bot, "_http_get_json", lambda url, headers=None: calls.append(url) or {"ok": 1})
    assert bot._fetch_metoffice_sync("hourly", 52.6, 1.3) == {"ok": 1}
    assert bot._fetch_metoffice_sync("hourly", 52.6, 1.3) == {"ok": 1}     # cached
    assert len(calls) == 1
    with pytest.raises(bot.QuotaError):
        bot._fetch_metoffice_sync("hourly", 50.0, -1.0)


def test_quota_reply(monkeypatch):
    monkeypatch.setattr(bot, "_geocode_sync", lambda q: (52.6, 1.3, "Norwich"))

    def over(kind, lat, lon):
        raise bot.QuotaError("300 calls used today")
    monkeypatch.setattr(bot, "_fetch_metoffice_sync", over)
    assert asyncio.run(bot.get_weather("")) == "WX: daily quota used, try tomorrow"


# ---------- weather formatting ----------
def _hourly(n=8):
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    series = [{
        "time": (start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%MZ"),
        "significantWeatherCode": 7, "screenTemperature": 13.2, "feelsLikeTemperature": 11.8,
        "screenRelativeHumidity": 73, "probOfPrecipitation": 10,
        "windDirectionFrom10m": 315, "windSpeed10m": 2.7, "windGustSpeed10m": 6.7,
    } for i in range(n)]
    return {"features": [{"properties": {"timeSeries": series}}]}


def test_format_hourly_plain(monkeypatch):
    monkeypatch.setattr(bot, "USE_EMOJI", False)
    out = bot.format_hourly(_hourly(), "Norwich")
    assert "Cloudy" in out and "13C feels 12C" in out
    assert "Wind NW 6mph gust 15" in out


def test_format_hours_respects_budget():
    out = bot.format_hours(_hourly(), "Norwich", hours=6, step=1, budget=80)
    assert len(out.encode("utf-8")) <= 80
    assert out.count("h ") >= 1


# ---------- scheduler ----------
def test_due_slot_daily():
    entry = {"time": "07:30", "days": ["mon"]}
    monday = datetime(2026, 9, 21, 7, 31, tzinfo=bot.TIMEZONE)
    assert bot.due_slot(entry, monday) == "time:2026-09-21 07:30"
    assert bot.due_slot(entry, monday + timedelta(minutes=5)) is None
    assert bot.due_slot(entry, monday + timedelta(days=1)) is None


def test_due_slot_every_minutes():
    entry = {"every_minutes": 360, "start": "00:00"}
    now = datetime(2026, 9, 21, 12, 1, tzinfo=bot.TIMEZONE)
    assert bot.due_slot(entry, now) == "every:2026-09-21 12:00"
    assert bot.due_slot(entry, now + timedelta(minutes=30)) is None


def test_due_slot_at_once():
    entry = {"at": "2026-12-25 09:00"}
    assert bot.due_slot(entry, datetime(2026, 12, 25, 9, 1, tzinfo=bot.TIMEZONE)) == "at:2026-12-25 09:00"
    assert bot.due_slot(entry, datetime(2027, 12, 25, 9, 1, tzinfo=bot.TIMEZONE)) is None


def test_due_slot_at_yearly():
    entry = {"at": "12-25 09:00"}
    assert bot.due_slot(entry, datetime(2026, 12, 25, 9, 1, tzinfo=bot.TIMEZONE)) == "at:2026-12-25 09:00"
    assert bot.due_slot(entry, datetime(2027, 12, 25, 9, 1, tzinfo=bot.TIMEZONE)) == "at:2027-12-25 09:00"
    assert bot.due_slot(entry, datetime(2026, 12, 24, 9, 1, tzinfo=bot.TIMEZONE)) is None
    leap = {"at": "02-29 09:00"}
    assert bot.due_slot(leap, datetime(2027, 3, 1, 9, 1, tzinfo=bot.TIMEZONE)) is None
    assert bot.due_slot(leap, datetime(2028, 2, 29, 9, 1, tzinfo=bot.TIMEZONE)) == "at:2028-02-29 09:00"


def test_validate_schedule_drops_bad_entries():
    entries = [
        {"name": "ok", "time": "07:30", "channel": 1, "text": "hi"},
        {"name": "yearly", "at": "12-25 09:00", "channel": 1, "text": "hi"},
        {"name": "bad-at", "at": "13-25 09:00", "channel": 1, "text": "hi"},
        {"name": "no-target", "time": "07:30", "text": "hi"},
        {"name": "bad-time", "time": "25:00", "channel": 1, "text": "hi"},
        {"name": "too-often", "every_minutes": 1, "channel": 1, "text": "hi"},
        {"name": "bad-day", "time": "07:30", "days": ["funday"], "channel": 1, "text": "hi"},
    ]
    assert [e["name"] for e in bot.validate_schedule(entries)] == ["ok", "yearly"]


# ---------- weather warnings ----------
LONDON = ZoneInfo("Europe/London")
WARN_FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Met Office warnings for East of England</title>
<item><title>Yellow warning of rain affecting East of England</title>
<description>Yellow warning of rain affecting East of England: Norfolk, Suffolk
valid from 0600 Sat 26 Sep to 2100 Sat 26 Sep</description></item>
<item><title>Amber warning of wind affecting East of England</title>
<description>Norfolk valid from 1800 Thu 24 Sep to 1200 Fri 25 Sep</description></item>
<item><title>Yellow warning of fog affecting East of England</title>
<description>valid from 0000 Tue 22 Sep to 0900 Tue 22 Sep</description></item>
</channel></rss>"""


def test_parse_warnings_feed(monkeypatch):
    monkeypatch.setattr(bot, "TIMEZONE", LONDON)
    now = datetime(2026, 9, 24, 14, tzinfo=LONDON)
    warnings = bot.parse_warnings_feed(WARN_FEED_XML, now)
    assert [(w["level"], w["hazard"]) for w in warnings] == [("yellow", "rain"), ("amber", "wind"), ("yellow", "fog")]
    assert warnings[0]["start"] == datetime(2026, 9, 26, 6, tzinfo=LONDON)
    assert warnings[0]["end"] == datetime(2026, 9, 26, 21, tzinfo=LONDON)


def test_warning_year_rolls_over(monkeypatch):
    monkeypatch.setattr(bot, "TIMEZONE", LONDON)
    t = bot._warn_time("06", "00", "02", "Jan", datetime(2026, 12, 30, tzinfo=LONDON))
    assert t.year == 2027


def test_format_warnings(monkeypatch):
    monkeypatch.setattr(bot, "TIMEZONE", LONDON)
    now = datetime(2026, 9, 24, 14, tzinfo=LONDON)
    warnings = bot.parse_warnings_feed(WARN_FEED_XML, now)
    out = bot.format_warnings(warnings, "ee", now=now)
    # Amber first, expired fog warning left out
    assert out == "⚠️ East of England: 🟠💨 Wind Thu 18:00-Fri 12:00 | 🟡🌧️ Rain Sat 06:00-21:00"
    short = bot.format_warnings(warnings, "ee", budget=70, now=now)
    assert short.endswith("+1 more") and len(short.encode("utf-8")) <= 70
    monkeypatch.setattr(bot, "USE_EMOJI", False)
    assert bot.format_warnings([], "ee", now=now) == "No weather warnings for East of England"


@pytest.mark.parametrize("postcode, code", [
    ({"country": "England", "region": "London"}, "se"),
    ({"country": "England", "region": "East of England"}, "ee"),
    ({"country": "Scotland", "region": None, "admin_district": "Glasgow City"}, "st"),
    ({"country": "Wales", "region": None}, "wl"),
    ({"country": "Isle of Man"}, "uk"),
])
def test_warn_region_from_postcode(monkeypatch, postcode, code):
    monkeypatch.setattr(bot, "_geo_cache", bot.TTLCache(60))
    monkeypatch.setattr(bot, "_geocode_sync", lambda q: (51.5, -0.1, "Somewhere"))
    monkeypatch.setattr(bot, "_http_get_json", lambda url, headers=None: {"result": [postcode]})
    assert bot._warn_region_sync("Somewhere") == code


def test_warn_region_code_needs_no_lookup(monkeypatch):
    monkeypatch.setattr(bot, "_geocode_sync", lambda q: pytest.fail("should not geocode"))
    assert bot._warn_region_sync("NW") == "nw"
    assert bot._warn_region_sync("uk") == "uk"


def test_warn_unknown_place_is_silent(monkeypatch):
    def not_found(query):
        raise bot.LocationError(f"'{query}' not found")
    monkeypatch.setattr(bot, "_geocode_sync", not_found)
    assert asyncio.run(bot.run_command("!warn", "nowhere", "Alice", {})) is None


# ---------- warning alerts ----------
def _warning(level, hazard, start, end):
    return {"level": level, "hazard": hazard, "area": "", "start": start, "end": end}


class _FakeSender:
    def __init__(self):
        self.sent = []

    def channel(self, ch, text):
        self.sent.append((ch, text))

    def dm(self, key, text):
        self.sent.append((key, text))


@pytest.fixture
def warn_feed(monkeypatch):
    """A feed the test can change, and a watcher that reads it."""
    monkeypatch.setattr(bot, "TIMEZONE", LONDON)
    monkeypatch.setattr(bot, "_muted_until", 0.0)
    monkeypatch.setattr(bot, "_warn_watch", bot.WarnWatcher())
    feed = {"ee": []}
    monkeypatch.setattr(bot, "_fetch_warnings_sync", lambda code: feed[code])
    return feed


NOW = datetime(2026, 9, 24, 14, tzinfo=LONDON)
RAIN = _warning("yellow", "rain", NOW + timedelta(hours=16), NOW + timedelta(hours=31))
WIND = _warning("amber", "wind", NOW + timedelta(hours=4), NOW + timedelta(hours=22))


def test_warn_starts_watch_on_its_channel(warn_feed, monkeypatch):
    monkeypatch.setattr(bot, "_warn_region_sync", lambda q: "ee")
    warn_feed["ee"] = [RAIN]
    reply = asyncio.run(bot.run_command("!warn", "", "Alice", {}, ("chan", 1)))
    assert reply.startswith("@[Alice] ⚠️ East of England:")
    assert list(bot._warn_watch.watches) == [("chan", 1, "ee")]
    # No target (the CLI or a schedule): no watch
    monkeypatch.setattr(bot, "_warn_watch", bot.WarnWatcher())
    asyncio.run(bot.run_command("!warn", "", "Alice", {}))
    assert not bot._warn_watch.watches


def test_watch_posts_only_on_change(warn_feed):
    watcher, out = bot._warn_watch, _FakeSender()
    warn_feed["ee"] = [RAIN]
    watcher.add("chan", 1, "ee", [RAIN], now=NOW)
    asyncio.run(watcher.check(out, now=NOW))
    assert out.sent == []                                   # nothing new since the !warn
    warn_feed["ee"] = [RAIN, WIND]
    asyncio.run(watcher.check(out, now=NOW))
    assert out.sent == [(1, "🔔 ⚠️ East of England: 🟠💨 Wind Thu 18:00-Fri 12:00 | 🟡🌧️ Rain Fri 06:00-21:00")]
    asyncio.run(watcher.check(out, now=NOW))
    assert len(out.sent) == 1                               # same again: no post
    warn_feed["ee"] = []                                    # cancelled early
    asyncio.run(watcher.check(out, now=NOW))
    assert out.sent[-1] == (1, "🔔 ✅ No weather warnings for East of England")


def test_watch_level_change_is_posted(warn_feed):
    watcher, out = bot._warn_watch, _FakeSender()
    watcher.add("dm", "a1b2c3d4e5f6", "ee", [WIND], now=NOW)
    warn_feed["ee"] = [{**WIND, "level": "red"}]
    asyncio.run(watcher.check(out, now=NOW))
    assert out.sent[0][0] == "a1b2c3d4e5f6" and "🔴💨 Wind" in out.sent[0][1]


def test_watch_ignores_warning_running_out(warn_feed):
    watcher, out = bot._warn_watch, _FakeSender()
    watcher.add("chan", 1, "ee", [WIND, RAIN], now=NOW)
    warn_feed["ee"] = [WIND, RAIN]
    later = NOW + timedelta(hours=23)                       # wind has ended, rain still on
    asyncio.run(watcher.check(out, now=later))
    warn_feed["ee"] = [RAIN]                                # feed drops the ended warning
    asyncio.run(watcher.check(out, now=later))
    assert out.sent == []


def test_watch_waits_for_mute_then_expires(warn_feed, monkeypatch):
    watcher, out = bot._warn_watch, _FakeSender()
    watcher.add("chan", 1, "ee", [], now=NOW)
    warn_feed["ee"] = [RAIN]
    monkeypatch.setattr(bot, "_muted_until", time.monotonic() + 600)
    asyncio.run(watcher.check(out, now=NOW))
    assert out.sent == []
    monkeypatch.setattr(bot, "_muted_until", 0.0)
    asyncio.run(watcher.check(out, now=NOW))                # the change is posted after the mute
    assert len(out.sent) == 1
    watcher.watches[("chan", 1, "ee")]["until"] = time.monotonic() - 1
    asyncio.run(watcher.check(out, now=NOW))
    assert not watcher.watches


def test_watch_limit(warn_feed, monkeypatch):
    monkeypatch.setattr(bot, "WARN_WATCH_MAX", 1)
    watcher = bot._warn_watch
    watcher.add("chan", 1, "ee", [], now=NOW)
    watcher.add("chan", 3, "ee", [], now=NOW)
    assert list(watcher.watches) == [("chan", 1, "ee")]
    watcher.add("chan", 1, "ee", [RAIN], now=NOW)           # renewing an existing watch still works
    assert len(watcher.watches[("chan", 1, "ee")]["seen"]) == 1


# ---------- sun and moon ----------
def test_sun_times_london_midsummer(monkeypatch):
    monkeypatch.setattr(bot, "TIMEZONE", LONDON)
    rise, set_, state = bot.sun_times(51.5074, -0.1278, date(2026, 6, 21))
    assert state == ""
    # Published times: 04:43 and 21:21 BST
    assert abs(rise - datetime(2026, 6, 21, 4, 43, tzinfo=LONDON)) < timedelta(minutes=2)
    assert abs(set_ - datetime(2026, 6, 21, 21, 21, tzinfo=LONDON)) < timedelta(minutes=2)


def test_sun_polar_day():
    assert bot.sun_times(78.2, 15.6, date(2026, 6, 21))[2] == "up"
    assert bot.sun_times(78.2, 15.6, date(2026, 12, 21))[2] == "down"


def test_format_sun(monkeypatch):
    monkeypatch.setattr(bot, "TIMEZONE", LONDON)
    out = bot.format_sun(51.5074, -0.1278, "London", date(2026, 6, 21))
    assert out.startswith("London Sun 21 Jun 🌅 04:4") and "🌇 21:2" in out and "daylight" in out
    monkeypatch.setattr(bot, "USE_EMOJI", False)
    assert "sunrise 04:4" in bot.format_sun(51.5074, -0.1278, "London", date(2026, 6, 21))


def test_next_full_moon():
    # Published full moon: 26 Sep 2026 16:49 UTC
    full = bot.next_moon_phase(datetime(2026, 9, 20, tzinfo=timezone.utc), 180)
    assert abs(full - datetime(2026, 9, 26, 16, 49, tzinfo=timezone.utc)) < timedelta(hours=1)


@pytest.mark.parametrize("when, emoji", [
    (datetime(2026, 9, 26, 17, tzinfo=timezone.utc), "🌕"),     # full
    (datetime(2026, 9, 23, 12, tzinfo=timezone.utc), "🌔"),     # waxing gibbous
    (datetime(2026, 9, 30, 12, tzinfo=timezone.utc), "🌖"),     # waning gibbous
    (datetime(2026, 10, 10, 16, tzinfo=timezone.utc), "🌑"),    # new
    (datetime(2026, 10, 14, 12, tzinfo=timezone.utc), "🌒"),    # waxing crescent
])
def test_moon_phase_emoji(when, emoji):
    idx, lit = bot.moon_phase(when)
    assert bot.MOON_PHASES[idx][0] == emoji
    assert 0 <= lit <= 100


def test_format_moon(monkeypatch):
    monkeypatch.setattr(bot, "TIMEZONE", LONDON)
    out = bot.format_moon(datetime(2026, 9, 24, 12, tzinfo=LONDON))
    assert out.startswith("🌔 Waxing gibbous") and "🌕 Full Sat 26 Sep" in out


# ---------- stats and uptime ----------
def test_duration():
    assert bot._duration(42) == "42s"
    assert bot._duration(125) == "2m"
    assert bot._duration(3 * 3600 + 60) == "3h 1m"
    assert bot._duration(2 * 86400 + 3600 + 120) == "2d 1h 2m"


def test_format_stats(monkeypatch):
    stats = bot.Stats()
    stats.commands = Counter({"!wx": 5, "ping": 3, "test": 1, "!roll": 1})
    stats.heard, stats.sent, stats.limited = 40, 9, 2
    monkeypatch.setattr(bot, "_stats", stats)
    out = bot.format_stats(battery_mv=4020)
    assert out.startswith("📊 Cmds 10 (wx 5, ping 3,")
    assert "Heard 40" in out and "Limited 2" in out and "🔋4.02V" in out
    assert bot.format_stats(budget=70).startswith("📊 Cmds 10 (wx 5) |")   # list shortened to fit
    short = bot.format_stats(budget=60)
    assert len(short.encode("utf-8")) <= 60 and "(" not in short


def test_stats_and_uptime_are_admin_only():
    for cmd in ("!stats", "!uptime", "!mute", "!unmute", "!say"):
        assert not bot.command_allowed(cmd, admin=False)
        assert bot.command_allowed(cmd, admin=True)
    assert bot.command_allowed("!wx", admin=False)
    assert "!stats" not in bot.HELP_TEXT and "!uptime" not in bot.HELP_TEXT


def test_mute_and_unmute(monkeypatch):
    monkeypatch.setattr(bot, "_muted_until", 0.0)
    assert bot.mute("") == "🔊 Not muted"
    assert bot.mute("30").startswith("🔇 Muted for 30m, until ")
    assert 29 * 60 < bot.mute_remaining() <= 30 * 60
    status = bot.mute("")
    assert status.startswith(("🔇 Muted, 29m left", "🔇 Muted, 30m left")) and "(until " in status
    assert bot.unmute() == "🔊 Unmuted"
    assert bot.mute_remaining() == 0
    bot.mute("90")
    assert bot.mute("0") == "🔊 Unmuted"


@pytest.mark.parametrize("arg", ["abc", "-5", "1441", "1.5"])
def test_mute_rejects_bad_minutes(monkeypatch, arg):
    monkeypatch.setattr(bot, "_muted_until", 0.0)
    assert bot.mute(arg).startswith("Use !mute")
    assert bot.mute_remaining() == 0


def test_muted_schedule_is_skipped(monkeypatch):
    sent = []
    fake = type("FakeSender", (), {"channel": lambda self, ch, t: sent.append((ch, t)),
                                   "dm": lambda self, k, t: sent.append((k, t))})()
    monkeypatch.setattr(bot, "_muted_until", time.monotonic() + 600)
    monkeypatch.setattr(bot, "due_slot", lambda entry, now: "slot-1")

    async def run_once():
        task = asyncio.create_task(bot.scheduler(fake, [{"name": "x", "time": "07:30", "channel": 1, "text": "hi"}]))
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(run_once())
    assert sent == []


def test_say(monkeypatch):
    sent = []
    fake = type("FakeSender", (), {"channel": lambda self, ch, t: sent.append((ch, t))})()
    monkeypatch.setattr(bot, "_tx", fake)
    assert bot.say("1 Net starts 20:00") == "📢 Queued for ch1"
    assert sent == [(1, "Net starts 20:00")]
    assert bot.say("1").startswith("Use !say")
    assert bot.say("one hello").startswith("Use !say")
    assert len(sent) == 1


def test_say_works_while_muted(monkeypatch):
    sent = []
    fake = type("FakeSender", (), {"channel": lambda self, ch, t: sent.append((ch, t))})()
    monkeypatch.setattr(bot, "_tx", fake)
    monkeypatch.setattr(bot, "_muted_until", time.monotonic() + 600)
    assert asyncio.run(bot.run_command("!say", "3 Hello", "", {})) == "📢 Queued for ch3"
    assert sent == [(3, "Hello")]


def test_uptime_reply(monkeypatch):
    stats = bot.Stats()
    stats.started -= 3700
    monkeypatch.setattr(bot, "_stats", stats)
    monkeypatch.setattr(bot, "_host_uptime", lambda: None)
    assert asyncio.run(bot.run_command("!uptime", "", "Alice", {})).startswith("@[Alice] ⏱️ Bot up 1h 1m")


# ---------- config.toml ----------
@pytest.fixture
def restore_settings():
    saved = {name: getattr(bot, name)
             for name in [*bot._CONFIG_SETTINGS, "MET_OFFICE_API_KEY", "MET_OFFICE_KEY_SOURCE"]}
    yield
    for name, value in saved.items():
        setattr(bot, name, value)
    bot._wx_cache.ttl = bot.WX_CACHE_SEC
    bot._geo_cache.ttl = bot.GEOCODE_CACHE_SEC
    bot._warn_cache.ttl = bot.WARN_CACHE_SEC


@pytest.mark.skipif(bot.tomllib is None, reason="needs Python 3.11+")
def test_load_config(tmp_path, restore_settings):
    path = tmp_path / "config.toml"
    path.write_text(
        'channel_idxs = [2]\n'
        'timezone = "UTC"\n'
        'wx_cache_sec = 600\n'
        'min_tx_gap_sec = 5\n'
        'rate_limit_per_user = [5, 30]\n'
        'admin_pubkeys = ["A1B2C3D4E5F6"]\n'
        '[locations]\nHome = [51.5, -0.1]\n'
        '[[scheduled_messages]]\nname = "x"\ntime = "08:00"\nchannel = 2\ntext = "hi"\n',
        encoding="utf-8")
    assert bot.load_config(str(path)) == str(path)
    assert bot.CHANNEL_IDXS == [2]
    assert str(bot.TIMEZONE) == "UTC"
    assert bot._wx_cache.ttl == 600
    assert bot.MIN_TX_GAP_SEC == 5.0
    assert bot.RATE_LIMIT_PER_USER == (5, 30.0)
    assert bot.ADMIN_PUBKEYS == {"A1B2C3D4E5F6"}
    assert bot.LOCATIONS == {"Home": (51.5, -0.1)}
    assert bot.SCHEDULED_MESSAGES[0]["name"] == "x"


@pytest.mark.skipif(bot.tomllib is None, reason="needs Python 3.11+")
@pytest.mark.parametrize("line", ['use_emoji = "yes"', 'timezone = "Mars/Base"', 'log_level = "LOUD"'])
def test_load_config_rejects_bad_values(tmp_path, restore_settings, line):
    path = tmp_path / "config.toml"
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        bot.load_config(str(path))


@pytest.mark.skipif(bot.tomllib is None, reason="needs Python 3.11+")
def test_api_key_from_config(tmp_path, restore_settings, monkeypatch):
    monkeypatch.delenv("METOFFICE_API_KEY", raising=False)
    path = tmp_path / "config.toml"
    path.write_text('metoffice_api_key = "  from-config  "\n', encoding="utf-8")
    bot.load_config(str(path))
    assert bot.MET_OFFICE_API_KEY == "from-config"
    assert bot.MET_OFFICE_KEY_SOURCE == str(path)


@pytest.mark.skipif(bot.tomllib is None, reason="needs Python 3.11+")
def test_api_key_env_wins_over_config(tmp_path, restore_settings, monkeypatch):
    monkeypatch.setenv("METOFFICE_API_KEY", "from-env")
    path = tmp_path / "config.toml"
    path.write_text('metoffice_api_key = "from-config"\n', encoding="utf-8")
    bot.load_config(str(path))
    assert bot.MET_OFFICE_API_KEY == "from-env"
    assert bot.MET_OFFICE_KEY_SOURCE == "METOFFICE_API_KEY"


def test_load_config_missing_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MESHPOTATO_CONFIG", raising=False)
    monkeypatch.setattr(bot, "CONFIG_FILE", str(tmp_path / "config.toml"))
    assert bot.load_config() is None
    with pytest.raises(SystemExit):
        bot.load_config(str(tmp_path / "missing.toml"))
