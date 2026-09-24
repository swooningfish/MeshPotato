import asyncio
import time
from datetime import datetime, timedelta, timezone

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
    assert any(a in bot.eightball("Will it rain?") for a in bot.EIGHTBALL_ANSWERS)


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


def test_validate_schedule_drops_bad_entries():
    entries = [
        {"name": "ok", "time": "07:30", "channel": 1, "text": "hi"},
        {"name": "no-target", "time": "07:30", "text": "hi"},
        {"name": "bad-time", "time": "25:00", "channel": 1, "text": "hi"},
        {"name": "too-often", "every_minutes": 1, "channel": 1, "text": "hi"},
        {"name": "bad-day", "time": "07:30", "days": ["funday"], "channel": 1, "text": "hi"},
    ]
    assert [e["name"] for e in bot.validate_schedule(entries)] == ["ok"]


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
