import asyncio
import json
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import meshpotato as mp


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
    ("!helpwx", ("!helpwx", "")),
    ("!help fun", ("!help", "fun")),
    ("!conv 10 mi km", ("!conv", "10 mi km")),
    ("!convert 20 c", ("!conv", "20 c")),
    ("conv 10 mi km", ("", "")),
    ("!ohm 12v 2a", ("!ohm", "12v 2a")),
    ("!vir 5v 220r", ("!ohm", "5v 220r")),
    ("!res 4k7", ("!res", "4k7")),
    ("!resistor brown black red", ("!res", "brown black red")),
    ("!helpconv", ("!helpconv", "")),
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
    ("!who", ("!who", "")),
    ("!heard 2", ("!who", "2")),
    ("!status Alice", ("!status", "Alice")),
    ("!seen bob", ("!status", "bob")),
    ("!bearing Alice", ("!bearing", "Alice")),
    ("!brg NR1", ("!bearing", "NR1")),
    ("!freq pmr", ("!freq", "pmr")),
    ("!helpnet", ("!helpnet", "")),
    ("!mail Bob see you at 8", ("!mail", "Bob see you at 8")),
    ("!msg @[Sam] hi", ("!mail", "@[Sam] hi")),
    ("who", ("", "")),
    ("!nothing", ("", "")),
    ("", ("", "")),
])
def test_parse_command(body, expected):
    assert mp.commands.parse_command(body) == expected


def test_split_sender():
    assert mp.text.split_sender("Alice: !wx Cromer") == ("Alice", "!wx Cromer")
    assert mp.text.split_sender("no sender") == ("", "no sender")


# ---------- trim ----------
def test_trim_short_text_unchanged():
    assert mp.text.trim("hello   world") == "hello world"


def test_trim_cuts_to_bytes_without_splitting_emoji():
    text = "☀️" * 50
    out = mp.text.trim(text, limit=20)
    assert len(out.encode("utf-8")) <= 20
    assert out.endswith("~")
    out.encode("utf-8")  # still valid UTF-8


# ---------- dice, coin, eight ball ----------
@pytest.mark.parametrize("arg, dice, sides", [("", 1, 6), ("d20", 1, 20), ("20", 1, 20), ("3d8", 3, 8)])
def test_roll_dice_in_range(arg, dice, sides):
    out = mp.tools.roll_dice(arg)
    total = int(out.rsplit(" ", 1)[1])
    assert dice <= total <= dice * sides


def test_roll_dice_modifier():
    out = mp.tools.roll_dice("2d6+3")
    assert "2d6+3" in out
    assert 5 <= int(out.rsplit(" ", 1)[1]) <= 15


@pytest.mark.parametrize("arg", ["11d6", "d1", "d1001", "banana"])
def test_roll_dice_rejects_bad_specs(arg):
    assert "=" not in mp.tools.roll_dice(arg)
    assert not mp.tools.roll_dice(arg).startswith("\U0001F3B2")


def test_eightball_needs_question():
    assert mp.tools.eightball("").startswith("Ask a question")
    reply = mp.tools.eightball("Will it rain?")
    assert any(a in reply for a in mp.config.EIGHTBALL_ANSWERS)


# ---------- path and signal info ----------
def test_parse_rx_log_uses_meshcore_fields():
    parsed = mp.rx.parse_rx_log_data({"path_len": 2, "path": "a1b2", "path_hash_size": 1, "snr": 7.5, "rssi": -85})
    assert parsed == {"path_len": 2, "path_nodes": ["a1", "b2"], "snr": 7.5, "rssi": -85}


def test_parse_rx_log_multibyte_hashes():
    parsed = mp.rx.parse_rx_log_data({"path_len": 2, "path": "a1b2c3d4", "path_hash_size": 2})
    assert parsed["path_nodes"] == ["a1b2", "c3d4"]


def test_parse_raw_packet_flood():
    # header 0x11 (route type 1 = flood), path byte 0x02, path a1 b2, then payload
    parsed = mp.rx.parse_rx_log_data({"payload": "1102a1b2ffff"})
    assert parsed == {"path_len": 2, "path_nodes": ["a1", "b2"]}


def test_parse_raw_packet_skips_transport_codes():
    # header 0x10 (route type 0 = transport flood), 4 transport bytes, path byte 0x01, path c3
    parsed = mp.rx.parse_rx_log_data({"payload": "100000000001c3ff"})
    assert parsed == {"path_len": 1, "path_nodes": ["c3"]}


def test_parse_raw_packet_truncated():
    assert mp.rx.parse_rx_log_data({"payload": "1105a1"}) == {}


def _set_latest_rx(**fields):
    mp.rx.latest_rx.clear()
    mp.rx.latest_rx.update(fields, at=time.monotonic())


def test_message_rx_info_uses_matching_rx_log():
    _set_latest_rx(path_len=2, path_nodes=["a1", "b2"], snr=6.0, rssi=-90)
    info = mp.rx.message_rx_info({"path_len": 2, "SNR": 7.25})
    assert info == {"path_len": 2, "path_nodes": ["a1", "b2"], "snr": 7.25, "rssi": -90}


def test_message_rx_info_ignores_rx_log_for_other_packet():
    _set_latest_rx(path_len=5, path_nodes=["aa"] * 5, snr=1.0, rssi=-120)
    info = mp.rx.message_rx_info({"path_len": 1})
    assert info == {"path_len": 1, "snr": None, "rssi": None}


def test_message_rx_info_ignores_stale_rx_log():
    mp.rx.latest_rx.clear()
    mp.rx.latest_rx.update(path_len=1, rssi=-80, at=time.monotonic() - 60)
    assert mp.rx.message_rx_info({"path_len": 1})["rssi"] is None


@pytest.mark.parametrize("msg", [{"path_len": 255}, {"path_len": 63, "path_hash_mode": 3}])
def test_message_rx_info_direct_route(msg):
    mp.rx.latest_rx.clear()
    info = mp.rx.message_rx_info(msg)
    assert info["direct"] is True
    assert mp.rx.format_hops(info) == "(direct route)"


def test_format_hops():
    assert mp.rx.format_hops({"path_len": 0}) == "(0 hops)"
    assert mp.rx.format_hops({"path_len": 1}) == "(1 hop)"
    assert mp.rx.format_hops({"path_len": 3, "path_nodes": ["a1"]}) == "(3 hops)"
    assert mp.rx.format_hops({}) == "(? hops)"


def test_format_rx_report(monkeypatch):
    info = {"path_len": 2, "path_nodes": ["a1", "b2"], "snr": 7.5, "rssi": -85}
    assert mp.rx.format_rx_report(info) == "🐸 (2 hops)"
    assert mp.rx.format_rx_report({"path_len": 1, "snr": 10.0}) == "🐸 (1 hop)"
    assert mp.rx.format_rx_report({"direct": True}) == "🐸 (direct route)"
    assert mp.rx.format_rx_report({}) == "🐸 (? hops)"
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert mp.rx.format_rx_report(info) == "(2 hops)"
    assert mp.rx.format_rx_report({"path_len": 0, "snr": 10.0}) == "(0 hops)"
    assert mp.rx.format_rx_report({}) == "(? hops)"


CONTACTS = {
    "a1ff": {"public_key": "A1FF00", "adv_name": "Norwich Cathedral RPT", "type": 2},
    "b2aa": {"public_key": "b2aa00", "adv_name": "Hill Top", "type": 2},
    "b2bb": {"public_key": "b2bb00", "adv_name": "Other Hill", "type": 2},     # b2 is ambiguous
    "c3cc": {"public_key": "c3cc00", "adv_name": "Bob's phone", "type": 1},    # companion, not a repeater
}


def test_repeater_names():
    names = mp.contacts.repeater_names(CONTACTS)
    assert names == {"a1ff00": "Norwich Cathedral RPT", "b2aa00": "Hill Top", "b2bb00": "Other Hill"}


def test_format_path_names_unique_matches_only():
    info = {"path_len": 3, "path_nodes": ["a1", "b2", "c3"]}
    out = mp.rx.format_path(info, mp.contacts.repeater_names(CONTACTS))
    assert out == "🛤️ 3 hops: a1 Norwich Cath › b2 › c3"
    assert mp.rx.format_path({"path_len": 2, "path_nodes": ["b2aa", "c3cc"]},
                           mp.contacts.repeater_names(CONTACTS)) == "🛤️ 2 hops: b2aa Hill Top › c3cc"


def test_format_path_fits_budget():
    nodes = [f"{i:02x}" for i in range(20)]
    info = {"path_len": 20, "path_nodes": nodes}
    names = {f"{i:02x}0000": f"Repeater{i}" for i in range(20)}
    out = mp.rx.format_path(info, names, budget=60)
    assert len(out.encode("utf-8")) <= 60
    assert out.startswith("🛤️ 20 hops: 00 › 01 › ") and out.endswith(" more")
    short = mp.rx.format_path({"path_len": 2, "path_nodes": ["a1", "b2"]}, names={"a1ff": "X" * 12}, budget=30)
    assert short == "🛤️ 2 hops: a1 › b2"                # names dropped before hops are


@pytest.mark.parametrize("info, expected", [
    ({"direct": True}, "🛤️ Direct route, the path isn't carried in the message"),
    ({"path_len": 0}, "🛤️ 0 hops, heard directly"),
    ({"path_len": 2}, "🛤️ 2 hops, path not reported"),
    ({}, "🛤️ Path unknown"),
])
def test_format_path_without_nodes(info, expected):
    assert mp.rx.format_path(info) == expected


def test_path_command_and_trace_alias(monkeypatch):
    monkeypatch.setattr(mp.state, "radio", type("Radio", (), {"contacts": CONTACTS})())
    assert mp.commands.parse_command("!trace") == ("!path", "")
    assert mp.commands.parse_command("path") == ("", "")
    info = {"path_len": 1, "path_nodes": ["a1"]}
    assert asyncio.run(mp.commands.run_command("!path", "", "Alice", info)) == "@[Alice] 🛤️ 1 hop: a1 Norwich Cath"
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    monkeypatch.setattr(mp.state, "radio", None)
    assert mp.rx.format_path({"path_len": 2, "path_nodes": ["a1", "b2"]}, mp.contacts.repeater_names()) == "Path 2 hops: a1 > b2"


AURORA_STATUS_XML = b"""<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<current_status api_version="0.2.5"><updated><datetime>2026-09-24T21:30:32+0000</datetime></updated>
<site_status project_id="project:AWN" site_id="site:AWN:SUM" status_id="amber"/></current_status>"""
AURORA_ACTIVITY_XML = b"""<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<site_activity api_version="0.2.5" project_id="project:AWN" site_id="site:AWN:SUM">
<lower_threshold status_id="yellow">50</lower_threshold>
<activity status_id="green"><datetime>2026-09-24T19:00:00+0000</datetime><value>27.2</value></activity>
<activity status_id="red"><datetime>2026-09-24T20:00:00+0000</datetime><value>212.6</value></activity>
<activity status_id="amber"><datetime>2026-09-24T21:00:00+0000</datetime><value>131.4</value></activity>
</site_activity>"""


def test_parse_aurora():
    assert mp.aurora.parse_aurora_status(AURORA_STATUS_XML) == "amber"
    assert mp.aurora.parse_aurora_activity(AURORA_ACTIVITY_XML) == [27.2, 212.6, 131.4]


def test_format_aurora(monkeypatch):
    data = {"status": "amber", "activity": [27.2, 212.6, 131.4]}
    assert mp.aurora.format_aurora(data) == \
        "🟠 Amber: Amber alert: possible aurora | 131nT now, 213nT peak 24h | AuroraWatch UK"
    assert mp.aurora.format_aurora({"status": "green", "activity": []}) == \
        "🟢 Green: No significant activity | AuroraWatch UK"
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert mp.aurora.format_aurora({"status": "red", "activity": [250.0]}) == \
        "Aurora Red: Red alert: aurora likely | 250nT now, 250nT peak 24h | AuroraWatch UK"


def test_aurora_is_cached_and_identified(monkeypatch):
    calls = []

    def fake_get(url, headers=None, accept=""):
        calls.append(headers)
        return AURORA_STATUS_XML if url == mp.aurora.AURORA_STATUS_URL else AURORA_ACTIVITY_XML
    monkeypatch.setattr(mp.net, "get", fake_get)
    monkeypatch.setattr(mp.aurora, "_aurora_cache", mp.net.TTLCache(mp.aurora.AURORA_MIN_CACHE_SEC))
    assert mp.commands.parse_command("!solar") == ("!aurora", "")
    first = asyncio.run(mp.commands.run_command("!aurora", "", "Alice", {}))
    assert first.startswith("@[Alice] 🟠 Amber:")
    asyncio.run(mp.commands.run_command("!aurora", "", "Bob", {}))
    assert len(calls) == 2                                  # one status + one activity, then cached
    assert all(h.get("Referer") for h in calls)


def test_aurora_lookup_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(mp.net, "get", boom)
    monkeypatch.setattr(mp.aurora, "_aurora_cache", mp.net.TTLCache(mp.aurora.AURORA_MIN_CACHE_SEC))
    assert asyncio.run(mp.aurora.get_aurora()) == "AURORA: lookup failed"


AIR_DATA = {
    "current": {"european_aqi": 22, "pm10": 9.4, "pm2_5": 4.6, "nitrogen_dioxide": 7.4, "ozone": 63.0},
    "hourly": {
        "time": [f"2026-05-{d:02d}T{h:02d}:00" for d in (24, 25) for h in range(24)],
        "grass_pollen": [0.0] * 20 + [45.0] + [0.0] * 27,        # 20:00 today
        "birch_pollen": [12.0] + [0.0] * 47,                      # 00:00 today, already past
        "alder_pollen": [0.0] * 30 + [3.2] + [0.0] * 17,          # 06:00 tomorrow
        "mugwort_pollen": [0.0] * 48,
        "ragweed_pollen": [None] * 48,
    },
}


@pytest.mark.parametrize("aqi, band", [(0, "Good"), (20, "Good"), (22, "Fair"), (55, "Moderate"),
                                       (79, "Poor"), (100, "Very poor"), (140, "Extremely poor"), (None, "Unknown")])
def test_eaqi_band(aqi, band):
    assert mp.air.eaqi_band(aqi) == band


def test_format_air(monkeypatch):
    assert mp.air.format_air(AIR_DATA, "Norwich") == \
        "🟢 Norwich air: Fair (EAQI 22) | PM2.5 5 PM10 9 NO2 7 O3 63 µg/m³ | Open-Meteo"
    assert mp.air.format_air(AIR_DATA, "Norwich", budget=60) == "🟢 Norwich air: Fair (EAQI 22) | Open-Meteo"
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert mp.air.format_air({"current": {}}, "Cromer") == "Cromer air: Unknown | Open-Meteo"


def test_pollen_peaks_next_24h():
    now = datetime(2026, 5, 24, 9, 30, tzinfo=LONDON)
    peaks = mp.air.pollen_peaks(AIR_DATA, now=now)
    assert peaks == {"Grass": 45.0, "Birch": 0.0, "Alder": 3.2, "Mugwort": 0.0}


def test_pollen_level(monkeypatch):
    monkeypatch.setattr(mp.config, "POLLEN_LEVELS", {"grass": [30, 50, 150]})
    assert [mp.air.pollen_level("Grass", g) for g in (10, 30, 49, 50, 150)] == \
        ["Low", "Moderate", "Moderate", "High", "Very high"]
    assert mp.air.pollen_level("Birch", 500) == ""


def test_format_pollen(monkeypatch):
    monkeypatch.setattr(mp.config, "POLLEN_LEVELS", {"grass": [30, 50, 150]})
    peaks = {"Grass": 45.0, "Birch": 0.0, "Alder": 3.2}
    assert mp.air.format_pollen(peaks, "Norwich") == \
        "🌼 Norwich pollen 24h: Grass 45 Moderate | Alder 3 grains/m³ | Open-Meteo"
    assert mp.air.format_pollen({"Grass": 0.2}, "Norwich") == "🌼 Norwich pollen 24h: None forecast | Open-Meteo"
    short = mp.air.format_pollen(peaks, "Norwich", budget=65)
    assert short == "🌼 Norwich pollen 24h: Grass 45 Moderate grains/m³ | Open-Meteo"


def test_aq_and_pollen_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(mp.geo, "_geocode_sync", lambda q: (52.6278, 1.2983, "Norwich"))
    monkeypatch.setattr(mp.air, "_air_cache", mp.net.TTLCache(60))
    monkeypatch.setattr(mp.net, "get_json", lambda url, headers=None: calls.append(url) or AIR_DATA)
    assert mp.commands.parse_command("!air Cromer") == ("!aq", "Cromer")
    assert mp.commands.parse_command("!pollen") == ("!pollen", "")
    assert asyncio.run(mp.commands.run_command("!aq", "", "Alice", {})).startswith("@[Alice] 🟢 Norwich air: Fair")
    assert asyncio.run(mp.commands.run_command("!pollen", "", "Alice", {})).startswith("@[Alice] 🌼 Norwich pollen 24h:")
    assert len(calls) == 1                                  # one request serves both, then cached
    assert "european_aqi" in calls[0] and "grass_pollen" in calls[0]


def test_aq_unknown_place_is_silent(monkeypatch):
    def not_found(query):
        raise mp.geo.LocationError(f"'{query}' not found")
    monkeypatch.setattr(mp.geo, "_geocode_sync", not_found)
    assert asyncio.run(mp.commands.run_command("!aq", "nowhere", "Alice", {})) is None
    assert asyncio.run(mp.air.get_air("nowhere", mode="pollen")) == "POLLEN: 'nowhere' not found"


HAMQSL_XML = b"""<?xml version="1.0" encoding="UTF-8" ?>
<solar><solardata>
<source url="http://www.hamqsl.com/solar.html">N0NBH</source>
<solarflux>112</solarflux><aindex> 19</aindex><kindex> 2</kindex><sunspots>124</sunspots>
<calculatedconditions>
<band name="80m-40m" time="day">Fair</band><band name="30m-20m" time="day">Good</band>
<band name="17m-15m" time="day">Fair</band><band name="12m-10m" time="day">Poor</band>
<band name="80m-40m" time="night">Good</band><band name="30m-20m" time="night">Good</band>
<band name="17m-15m" time="night">Fair</band><band name="12m-10m" time="night">Poor</band>
</calculatedconditions>
<calculatedvhfconditions>
<phenomenon name="vhf-aurora" location="northern_hemi">Band Closed</phenomenon>
<phenomenon name="E-Skip" location="europe">Band Closed</phenomenon>
<phenomenon name="E-Skip" location="europe_6m">50MHz ES</phenomenon>
<phenomenon name="E-Skip" location="europe_4m">Band Closed</phenomenon>
</calculatedvhfconditions>
<geomagfield>QUIET</geomagfield><signalnoise>S1-S2</signalnoise>
</solardata></solar>"""


def test_parse_hamqsl():
    data = mp.bands.parse_hamqsl(HAMQSL_XML)
    assert (data["sfi"], data["a"], data["k"], data["noise"]) == ("112", "19", "2", "S1-S2")
    assert data["hf"][("30m-20m", "night")] == "Good"
    assert data["vhf"][("E-Skip", "europe_6m")] == "50MHz ES"


def test_format_hf(monkeypatch):
    data = mp.bands.parse_hamqsl(HAMQSL_XML)
    assert mp.bands.format_hf(data, day=True) == \
        "📻 HF day: 80-40m🟡 30-20m🟢 17-15m🟡 12-10m🔴 | ☀️SFI 112 🧲K2 A19 | 🔊S1-S2 | N0NBH"
    assert mp.bands.format_hf(data, day=False, budget=100) == \
        "📻 HF night: 80-40m🟢 30-20m🟢 17-15m🟡 12-10m🔴 | ☀️SFI 112 🧲K2 A19 | N0NBH"     # noise dropped
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert mp.bands.format_hf(data, day=True) == ("HF day: 80-40m Fair | 30-20m Good | 17-15m Fair | "
                                             "12-10m Poor | SFI 112 K2 A19 | Noise S1-S2 | N0NBH")


def test_format_vhf(monkeypatch):
    data = mp.bands.parse_hamqsl(HAMQSL_XML)
    assert mp.bands.format_vhf(data, "Normal") == \
        "📡 VHF: 6m Es🟢 50MHz ES 4m Es🔴 2m Es🔴 Aurora🔴 | Tropo ➖Normal | N0NBH, Open-Meteo"
    assert mp.bands.format_vhf(data, "Normal", budget=80) == "📡 VHF: 6m Es🟢 50MHz ES 4m Es🔴 2m Es🔴 Aurora🔴 | N0NBH"
    assert mp.bands.format_vhf(data, None) == "📡 VHF: 6m Es🟢 50MHz ES 4m Es🔴 2m Es🔴 Aurora🔴 | N0NBH"
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert mp.bands.format_vhf(data, "Normal") == \
        "VHF: 6m Es 50MHz ES | 4m Es Closed | 2m Es Closed | Aurora Closed | Tropo Normal | N0NBH, Open-Meteo"


def test_is_daytime(monkeypatch):
    monkeypatch.setattr(mp.config, "TIMEZONE", LONDON)
    assert mp.bands.is_daytime(52.63, 1.30, datetime(2026, 9, 24, 12, tzinfo=LONDON))
    assert not mp.bands.is_daytime(52.63, 1.30, datetime(2026, 9, 24, 23, tzinfo=LONDON))


def test_refractivity_standard_values():
    # Sea level, 15°C, 1013 hPa, 60% humidity: about 320 N-units
    assert 315 < mp.bands.refractivity(15, 60, 1013) < 330
    assert mp.bands.refractivity(15, 0, 1013) == pytest.approx(77.6 / 288.15 * 1013)


@pytest.mark.parametrize("gradient, level", [(10, "Below normal"), (-40, "Normal"), (-70, "Slightly enhanced"),
                                             (-100, "Enhanced"), (-200, "Ducting likely")])
def test_tropo_level(gradient, level):
    assert mp.bands.tropo_level(gradient) == level


def _tropo_data(t925_values, rh925_values=None):
    n = len(t925_values)
    start = datetime(2026, 9, 24)
    return {"elevation": 25.0, "hourly": {
        "time": [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(n)],
        "temperature_2m": [10.0] * n, "relative_humidity_2m": [80.0] * n, "surface_pressure": [1020.0] * n,
        "temperature_925hPa": t925_values, "relative_humidity_925hPa": rh925_values or [40.0] * n,
        "geopotential_height_925hPa": [800.0] * n}}


def test_tropo_outlook_finds_inversion():
    # Warm, dry air above cool, moist air (an inversion) bends UHF back down
    data = _tropo_data([5.0] * 6 + [16.0] + [5.0] * 17, [40.0] * 6 + [10.0] + [40.0] * 17)
    grads = mp.bands.tropo_gradients(data)
    assert len(grads) == 24
    outlook = mp.bands.tropo_outlook(data, now=datetime(2026, 9, 24, 0, tzinfo=LONDON))
    assert outlook["best_at"] == datetime(2026, 9, 24, 6)
    assert outlook["best"] < outlook["now"]
    text = mp.bands.format_uhf(outlook, "Norwich")
    assert text.startswith("📶 Norwich UHF tropo: 🔼Slightly enhanced now") and "24h best ⏫Enhanced Thu 06h" in text
    assert mp.bands.format_uhf(None, "Norwich") == "📶 Norwich UHF tropo: no forecast data | Open-Meteo"


def test_tropo_skips_high_ground():
    data = _tropo_data([5.0])
    data["elevation"] = 790.0                       # ground almost at the 925 hPa level
    assert mp.bands.tropo_gradients(data) == []


def test_radio_commands(monkeypatch):
    fetches = []
    monkeypatch.setattr(mp.bands, "_hamqsl_cache", mp.net.TTLCache(mp.bands.HAMQSL_MIN_CACHE_SEC))
    monkeypatch.setattr(mp.bands, "_tropo_cache", mp.net.TTLCache(60))
    monkeypatch.setattr(mp.geo, "_geocode_sync", lambda q: (52.6278, 1.2983, "Norwich"))
    monkeypatch.setattr(mp.net, "get", lambda url, headers=None, accept="": fetches.append(url) or HAMQSL_XML)
    monkeypatch.setattr(mp.net, "get_json", lambda url, headers=None: fetches.append(url) or _tropo_data([5.0] * 48))
    assert mp.commands.parse_command("!bands") == ("!hf", "")
    assert mp.commands.parse_command("!tropo Cromer") == ("!uhf", "Cromer")
    assert asyncio.run(mp.commands.run_command("!hf", "", "Alice", {})).startswith("@[Alice] 📻 HF ")
    assert asyncio.run(mp.commands.run_command("!vhf", "", "Alice", {})).startswith("@[Alice] 📡 VHF: 6m Es")
    assert asyncio.run(mp.commands.run_command("!uhf", "", "Alice", {})).startswith("@[Alice] 📶 Norwich UHF tropo:")
    assert sum(u == mp.bands.HAMQSL_URL for u in fetches) == 1       # one N0NBH fetch serves !hf and !vhf
    assert sum(u.startswith(mp.bands.TROPO_API) for u in fetches) == 1


def test_radio_lookup_failures(monkeypatch):
    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(mp.bands, "_hamqsl_cache", mp.net.TTLCache(mp.bands.HAMQSL_MIN_CACHE_SEC))
    monkeypatch.setattr(mp.bands, "_tropo_cache", mp.net.TTLCache(60))
    monkeypatch.setattr(mp.net, "get", boom)
    monkeypatch.setattr(mp.net, "get_json", boom)
    monkeypatch.setattr(mp.geo, "_geocode_sync", lambda q: (52.6278, 1.2983, "Norwich"))
    assert asyncio.run(mp.bands.get_hf()) == "HF: lookup failed"
    assert asyncio.run(mp.bands.get_vhf()) == "VHF: lookup failed"
    assert asyncio.run(mp.bands.get_uhf("")) == "UHF: lookup failed"


NORWICH, CROMER = (52.6278, 1.2983), (52.9310, 1.3020)
GPS_CONTACTS = {
    "a1": {"public_key": "a1ff00", "adv_name": "Aylsham RPT", "type": 2, "adv_lat": 52.7960, "adv_lon": 1.2540},
    "b2": {"public_key": "b2aa00", "adv_name": "No GPS RPT", "type": 2, "adv_lat": 0.0, "adv_lon": 0.0},
    "c3": {"public_key": "c3cc00", "adv_name": "Hellesdon RPT", "type": 2, "adv_lat": 52.6620, "adv_lon": 1.2500},
    "al": {"public_key": "d4dd00", "adv_name": "Alice", "type": 1, "adv_lat": CROMER[0], "adv_lon": CROMER[1]},
}


def test_haversine_km():
    assert mp.geo.haversine_km(NORWICH, CROMER) == pytest.approx(33.7, abs=0.2)
    assert mp.geo.haversine_km(NORWICH, NORWICH) == 0


def test_positions():
    assert mp.geo.repeater_position("a1", GPS_CONTACTS) == (52.7960, 1.2540)
    assert mp.geo.repeater_position("b2", GPS_CONTACTS) is None                 # no GPS in its advert
    assert mp.geo.repeater_position("d4", GPS_CONTACTS) is None                 # a companion, not a repeater
    assert mp.geo.repeater_position("ee", GPS_CONTACTS) is None                 # unknown
    assert mp.geo.sender_position(GPS_CONTACTS, name="alice") == CROMER
    assert mp.geo.sender_position(GPS_CONTACTS, key_prefix="D4DD") == CROMER
    assert mp.geo.sender_position(GPS_CONTACTS, name="Bob") is None


def test_bot_position(monkeypatch):
    monkeypatch.setattr(mp.config, "LOCATIONS", {"Norwich": NORWICH})
    monkeypatch.setattr(mp.config, "DEFAULT_LOCATION", "Norwich")
    assert mp.geo.bot_position({"adv_lat": 52.0, "adv_lon": 1.0}) == (52.0, 1.0)
    assert mp.geo.bot_position({"adv_lat": 0.0, "adv_lon": 0.0}) == NORWICH     # falls back to DEFAULT_LOCATION
    monkeypatch.setattr(mp.config, "DEFAULT_LOCATION", "Somewhere")
    assert mp.geo.bot_position({}) is None


def test_format_dist(monkeypatch):
    info = {"path_len": 3, "path_nodes": ["a1", "b2", "c3"]}
    out = mp.geo.format_dist(info, GPS_CONTACTS, CROMER, NORWICH)
    assert out == "📏 You ›15km› a1 ›?› b2 ›?› c3 ›5.0km› Bot | 20km+, 2 of 4 legs unknown | 34km direct"
    full = mp.geo.format_dist({"path_len": 2, "path_nodes": ["a1", "c3"]}, GPS_CONTACTS, CROMER, NORWICH)
    assert full == "📏 You ›15km› a1 ›15km› c3 ›5.0km› Bot | 35km total | 34km direct"
    monkeypatch.setattr(mp.config, "DIST_MILES", True)
    assert mp.geo.format_dist({"path_len": 2, "path_nodes": ["a1", "c3"]}, GPS_CONTACTS, CROMER, NORWICH) == \
        "📏 You ›9.5mi› a1 ›9.3mi› c3 ›3.1mi› Bot | 22mi total | 21mi direct"


def test_format_dist_fits_budget():
    info = {"path_len": 8, "path_nodes": ["a1", "c3"] * 4}
    out = mp.geo.format_dist(info, GPS_CONTACTS, CROMER, NORWICH, budget=70)
    assert len(out.encode("utf-8")) <= 70 and out.startswith("📏 8 hops: ") and "direct" in out


# ---------- !bearing ----------
def test_initial_bearing():
    assert mp.geo.initial_bearing(NORWICH, CROMER) == pytest.approx(0.4, abs=0.5)        # almost due north
    assert mp.geo.initial_bearing(CROMER, NORWICH) == pytest.approx(180.4, abs=0.5)
    assert mp.geo.initial_bearing((52.0, 1.0), (52.0, 1.5)) == pytest.approx(90, abs=0.5)


def test_bearing_to_contact(monkeypatch):
    monkeypatch.setattr(mp.state, "radio", type("Radio", (), {"self_info": {}})())
    monkeypatch.setattr(mp.config, "LOCATIONS", {"Norwich": NORWICH})
    monkeypatch.setattr(mp.config, "DEFAULT_LOCATION", "Norwich")
    # From the sender when their position is known, else from the bot
    assert asyncio.run(mp.geo.get_bearing("aylsham", GPS_CONTACTS, CROMER)) == "🧭 Aylsham RPT: 15km S (192°) from you"
    assert asyncio.run(mp.geo.get_bearing("Alice", GPS_CONTACTS, None)) == "🧭 Alice: 34km N (0°) from the bot"
    assert asyncio.run(mp.geo.get_bearing("no gps", GPS_CONTACTS, CROMER)) == "🧭 No GPS RPT doesn't share a position"
    assert asyncio.run(mp.geo.get_bearing("alice", GPS_CONTACTS, CROMER)) == "🧭 Alice is right by you"
    assert asyncio.run(mp.geo.get_bearing("rpt", GPS_CONTACTS, CROMER)).startswith("🧭 3 contacts match 'rpt': ")
    assert asyncio.run(mp.geo.get_bearing("", GPS_CONTACTS, CROMER)).startswith("🧭 Use !bearing")


def test_bearing_to_place(monkeypatch):
    monkeypatch.setattr(mp.config, "LOCATIONS", {"Norwich": NORWICH})
    # Not a contact, so it is looked up as a place: saved names and lat,lon work offline
    assert asyncio.run(mp.geo.get_bearing("norwich", GPS_CONTACTS, CROMER)) == "🧭 Norwich: 34km S (180°) from you"
    assert asyncio.run(mp.geo.get_bearing("52.93,1.50", {}, CROMER)) == "🧭 52.93,1.50: 13km E (90°) from you"

    def not_found(q):
        raise mp.geo.LocationError("not found")
    monkeypatch.setattr(mp.geo, "_geocode_sync", not_found)
    assert asyncio.run(mp.geo.get_bearing("Atlantis", GPS_CONTACTS, CROMER)) == "🧭 No contact or place called 'Atlantis'"

    def offline(q):
        raise OSError("no network")
    monkeypatch.setattr(mp.geo, "_geocode_sync", offline)
    assert asyncio.run(mp.geo.get_bearing("Cromer", {}, CROMER)) == "🧭 'Cromer' isn't a contact and the place lookup failed"


def test_bearing_command_uses_dm_sender_position(monkeypatch):
    monkeypatch.setattr(mp.state, "radio", type("Radio", (), {"contacts": GPS_CONTACTS, "self_info": {}})())
    out = asyncio.run(mp.commands.run_command("!bearing", "hellesdon", "", {}, ("dm", "d4dd00")))
    assert out == "🧭 Hellesdon RPT: 30km S (187°) from you"


# ---------- !who and !status ----------
HEARD_NOW = 1_800_000_000.0


def _heard():
    h = mp.heard.Heard()
    h.record("Alice", "dm", "a1a1", {"path_len": 2, "snr": 7.5}, now=HEARD_NOW - 120)
    h.record("Bob", "dm", "B0B0", {"direct": True, "snr": -3.25}, now=HEARD_NOW - 3 * 3600)
    h.record("Aylsham RPT", "advert", "a1ff00", repeater=True, now=HEARD_NOW - 600)
    h.record("Carol", "advert", "c4c4", now=HEARD_NOW - 30 * 3600)
    return h


def test_heard_keyed_by_public_key():
    h = _heard()
    assert set(h.nodes) == {"a1a1", "b0b0", "a1ff00", "c4c4"}
    # A key is required: a name on its own is never recorded
    h.record("Mallory", "advert", "")
    h.record("  ", "dm", "d4d4")
    assert len(h.nodes) == 4


def test_heard_rename_with_same_key():
    h = mp.heard.Heard()
    h.record("Old Name", "dm", "D4DD00AABBCCDDEE", now=HEARD_NOW - 60)
    h.record("New Name", "advert", "d4dd00aabbcc", now=HEARD_NOW)
    assert h.nodes == {"d4dd00aabbcc": {"name": "New Name", "at": HEARD_NOW, "via": "advert", "key": "d4dd00aabbcc"}}


def test_heard_load_drops_name_only_entries(tmp_path):
    path = tmp_path / "heard.json"
    path.write_text(json.dumps([
        {"name": "Sam 🐬 Base Camp", "at": HEARD_NOW, "via": "ch1"},             # older version: channel name
        {"name": "Old Name", "at": HEARD_NOW - 99, "via": "dm", "key": "d4dd00aabbcc"},
        {"name": "New Name", "at": HEARD_NOW - 5, "via": "advert", "key": "d4dd00aabbcc"},
        {"name": 42, "at": HEARD_NOW, "key": "e5e5"},
        {"name": "Bad key", "at": HEARD_NOW, "key": "not hex"},
        "junk",
    ]), encoding="utf-8")
    h = mp.heard.Heard()
    h.load(str(path))
    assert [e["name"] for e in h.nodes.values()] == ["New Name"]
    assert h.dirty          # so the next save writes the file without them


def test_format_who():
    h = _heard()
    assert mp.heard.format_who(h, now=HEARD_NOW) == "👥 2 heard in 24h: Alice 2m, Bob 3h"
    assert mp.heard.format_who(h, "48", now=HEARD_NOW) == "👥 3 heard in 48h: Alice 2m, Bob 3h, Carol 30h"
    assert mp.heard.format_who(h, "1h", now=HEARD_NOW) == "👥 1 heard in 1h: Alice 2m"
    assert mp.heard.format_who(h, "rpt", now=HEARD_NOW) == "👥 1 repeater heard in 24h: Aylsham RPT 10m"
    assert mp.heard.format_who(mp.heard.Heard(), now=HEARD_NOW) == "👥 No people heard in the last 24h"


def test_format_who_full_names_when_they_fit():
    h = mp.heard.Heard()
    h.record("Sam 🐬 Base Camp", "advert", "5a5a", now=HEARD_NOW)
    h.record("Alice in Norwich", "dm", "a1a1", now=HEARD_NOW - 60)
    assert mp.heard.format_who(h, now=HEARD_NOW) == "👥 2 heard in 24h: Sam 🐬 Base Camp 0s, Alice in Norwich 1m"
    assert mp.heard.format_who(h, now=HEARD_NOW, budget=60) == "👥 2 heard in 24h: Sam 🐬 Base C 0s, Alice in Nor 1m"


def test_format_who_fits_budget():
    h = mp.heard.Heard()
    for i in range(30):
        h.record(f"Node number {i:02d}", "advert", f"aa{i:02d}", now=HEARD_NOW - i * 60)
    out = mp.heard.format_who(h, now=HEARD_NOW, budget=80)
    assert len(out.encode("utf-8")) <= 80 and out.startswith("👥 30 heard in 24h: Node number 0s")
    assert out.endswith(" more")


def test_format_status():
    h = _heard()
    assert mp.heard.format_status("alice", h, {}, NORWICH, now=HEARD_NOW) ==         "👤 Alice: heard 2m ago by DM, 2 hops, SNR 7.5dB"
    assert mp.heard.format_status("bob", h, {}, NORWICH, now=HEARD_NOW) ==         "👤 Bob: heard 3h ago by DM, direct route, SNR -3.25dB"
    # The position comes from the contact with that key
    assert mp.heard.format_status("aylsham", h, GPS_CONTACTS, NORWICH, now=HEARD_NOW) ==         "👤 Aylsham RPT: heard 10m ago by advert | 📍 19km N of bot"
    assert mp.heard.format_status("a", h, {}, NORWICH, now=HEARD_NOW) == "👤 2 match 'a': Alice a1a1, Aylsham RPT a1ff"
    assert mp.heard.format_status("nobody", h, GPS_CONTACTS, NORWICH, now=HEARD_NOW) == "👤 No one called 'nobody' heard"
    assert mp.heard.format_status("", h, {}, NORWICH, now=HEARD_NOW).startswith("👤 Use !status")


def test_format_status_same_name_two_radios():
    h = _heard()
    h.record("Bob", "advert", "b1b1", now=HEARD_NOW - 7200)
    assert mp.heard.format_status("bob", h, {}, NORWICH, now=HEARD_NOW) == "👤 2 match 'bob': Bob b0b0, Bob b1b1"
    assert mp.heard.format_status("b1b1", h, {}, NORWICH, now=HEARD_NOW) == "👤 Bob: heard 2h ago by advert"
    contacts = {"x": {"public_key": "e5e5e5", "adv_name": "Dave", "type": 1, "last_advert": HEARD_NOW - 60}}
    assert mp.heard.format_status("e5e5", h, contacts, NORWICH, now=HEARD_NOW) == "👤 Dave: last advert 1m ago"


def test_format_status_from_contact_advert():
    contacts = {"x": {"public_key": "e5e5", "adv_name": "Dave", "type": 1, "last_advert": HEARD_NOW - 7200},
                "y": {"public_key": "f6f6", "adv_name": "Eve", "type": 1, "last_advert": HEARD_NOW + 999999}}
    assert mp.heard.format_status("dave", mp.heard.Heard(), contacts, NORWICH, now=HEARD_NOW) == "👤 Dave: last advert 2h ago"
    # A node with a wrong clock sends an advert time in the future
    assert mp.heard.format_status("eve", mp.heard.Heard(), contacts, NORWICH, now=HEARD_NOW) == "👤 Eve: in contacts, not heard yet"


def test_heard_save_load_prune(tmp_path, monkeypatch):
    path = str(tmp_path / "heard.json")
    h = _heard()
    h.save(path)
    assert not h.dirty
    loaded = mp.heard.Heard()
    loaded.load(path)
    assert loaded.nodes == h.nodes and not loaded.dirty
    monkeypatch.setattr(mp.config, "HEARD_KEEP_DAYS", 1)
    loaded.prune(now=HEARD_NOW)
    assert set(loaded.nodes) == {"a1a1", "b0b0", "a1ff00"}
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    broken = mp.heard.Heard()
    broken.load(str(tmp_path / "bad.json"))
    broken.load(str(tmp_path / "missing.json"))
    assert broken.nodes == {}


def test_save_state(tmp_path):
    heard_file, mail_file = str(tmp_path / "heard.json"), str(tmp_path / "mail.json")
    h, box = _heard(), mp.mail.Mailbox()
    box.add("Alice", "name:alice", "Sam", "d4dd00", "hello", now=HEARD_NOW)
    assert mp.commands.save_state(h, box, heard_file, mail_file) == "💾 4 nodes saved, 1 message saved"
    assert mp.commands.save_state(h, box, heard_file, mail_file) == "💾 4 nodes unchanged, 1 message unchanged"
    h.record("Zed", "advert", "e0e0")
    bad = str(tmp_path / "missing_dir" / "heard.json")
    assert mp.commands.save_state(h, box, bad, mail_file) == "💾 heard.json failed, 1 message unchanged, see the log"
    assert h.dirty


# ---------- !mail ----------
MAIL_CONTACTS = {
    "s": {"public_key": "5a5a5a5a5a5a0000", "adv_name": "Sam 🐬 Base", "type": 1},
    "b": {"public_key": "b0b0b0b0b0b00000", "adv_name": "Bob", "type": 1},
    "b2": {"public_key": "b1b1b1b1b1b10000", "adv_name": "Bobby Two", "type": 1},
    "r": {"public_key": "a1ff00a1ff000000", "adv_name": "Sam Hill RPT", "type": 2},
}


@pytest.mark.parametrize("arg, name, text", [
    ("Bob see you at 8", "Bob", "see you at 8"),
    ("bob see you at 8", "Bob", "see you at 8"),
    ("Sam 🐬 Base on my way", "Sam 🐬 Base", "on my way"),
    ("sam base on my way", "Sam 🐬 Base", "on my way"),        # emoji can be left out
    ("@[Sam 🐬 Base] on my way", "Sam 🐬 Base", "on my way"),
    ("sam on my way", "Sam 🐬 Base", "on my way"),             # start of one name (repeaters don't count)
    ("Bobby Two hi", "Bobby Two", "hi"),
])
def test_mail_recipient(arg, name, text):
    contact, msg, error = mp.mail.mail_recipient(arg, MAIL_CONTACTS)
    assert (mp.contacts.contact_name(contact), msg, error) == (name, text, "")


def test_mail_recipient_errors():
    assert mp.mail.mail_recipient("bo hi", MAIL_CONTACTS)[2] == "2 contacts match 'bo': Bob, Bobby Two"
    assert mp.mail.mail_recipient("zed hi", MAIL_CONTACTS)[2] == "No contact called 'zed'"
    assert mp.mail.mail_recipient("@[Zed] hi", MAIL_CONTACTS)[2] == "No contact called 'Zed'"
    assert mp.mail.mail_recipient("bob", MAIL_CONTACTS) == (None, "", "")


def test_mail_queue_and_limits(monkeypatch):
    box = mp.mail.Mailbox()
    assert mp.mail.leave_mail("bob see you at 8", "Alice", "name:alice", MAIL_CONTACTS, box) == \
        "📮 Held for Bob (1/10), sent by DM when the bot next hears them"
    assert box.messages[0]["to_key"] == "b0b0b0b0b0b0" and box.dirty
    for i in range(9):
        mp.mail.leave_mail(f"bob msg {i}", "Alice", "name:alice", MAIL_CONTACTS, box)
    assert mp.mail.leave_mail("bob one more", "Alice", "name:alice", MAIL_CONTACTS, box) == \
        "📮 Bob already has 10 from you waiting, the most allowed"
    # The limit is per sender: someone else can still leave Bob a message
    assert mp.mail.leave_mail("bob hi", "Carol", "name:carol", MAIL_CONTACTS, box).startswith("📮 Held for Bob (1/10)")
    assert mp.mail.leave_mail("", "Alice", "name:alice", MAIL_CONTACTS, box) == "📮 Use !mail <name> <message>. Waiting: Bob 10. !clearmail to cancel"
    assert mp.mail.leave_mail("bob " + "x" * 101, "Alice", "name:alice", MAIL_CONTACTS, box) == \
        "📮 Too long, 101 bytes. The most is 100"
    assert mp.mail.leave_mail("bob", "Alice", "name:alice", MAIL_CONTACTS, box) == "📮 Use !mail <name> <message>"
    monkeypatch.setattr(mp.config, "MAIL_MAX_TOTAL", 11)
    assert mp.mail.leave_mail("sam hi", "Dee", "name:dee", MAIL_CONTACTS, box) == "📮 The mailbox is full, try again later"


def test_mail_take_for():
    box = mp.mail.Mailbox()
    box.add("Alice", "name:alice", "Sam 🐬 Base", "5a5a5a5a5a5a", "one", now=HEARD_NOW - 60)
    box.add("Carol", "name:carol", "Sam 🐬 Base", "5a5a5a5a5a5a", "two", now=HEARD_NOW - 30)
    box.add("Alice", "name:alice", "Bob", "b0b0b0b0b0b0", "for bob", now=HEARD_NOW)
    box.dirty = False
    assert box.take_for("c0c0c0c0c0c0") == [] and box.take_for("") == [] and not box.dirty
    # Matched on the key only, full or cut to 12 characters
    assert [m["text"] for m in box.take_for("5A5A5A5A5A5A5A5A")] == ["one", "two"]
    assert box.dirty and len(box.messages) == 1
    assert [m["text"] for m in box.take_for("b0b0b0b0b0b0")] == ["for bob"]
    assert box.messages == []


def test_format_mail():
    m = {"from": "Alice", "text": "see you at 8", "at": HEARD_NOW - 7200}
    assert mp.mail.format_mail(m, now=HEARD_NOW) == "📬 Alice 2h ago: see you at 8"
    long = {"from": "N" * 32, "text": "x" * mp.config.MAIL_MAX_BYTES, "at": HEARD_NOW - 86400 * 6}
    assert len(mp.text.trim(mp.mail.format_mail(long, now=HEARD_NOW)).encode("utf-8")) <= mp.config.MAX_REPLY_BYTES
    assert mp.mail.format_mail(long, now=HEARD_NOW).endswith("x" * mp.config.MAIL_MAX_BYTES)    # fits without cutting


def test_mailbox_prune_save_load(tmp_path, monkeypatch):
    path = str(tmp_path / "mail.json")
    box = mp.mail.Mailbox()
    box.add("Alice", "name:alice", "Bob", "b0b0", "new", now=HEARD_NOW)
    box.add("Alice", "name:alice", "Bob", "b0b0", "old", now=HEARD_NOW - 8 * 86400)
    box.prune(now=HEARD_NOW)
    assert [m["text"] for m in box.messages] == ["new"]
    assert box.save(path) and not box.dirty
    loaded = mp.mail.Mailbox()
    loaded.load(path)
    assert loaded.messages == box.messages
    (tmp_path / "bad.json").write_text('[{"from": "x"}, "junk"]', encoding="utf-8")
    loaded.load(str(tmp_path / "bad.json"))
    assert loaded.messages == []


def _mail_setup(monkeypatch, approved=("b0b0b0b0b0b0", "5a5a5a5a5a5a")):
    monkeypatch.setattr(mp.state, "radio", type("Radio", (), {"contacts": MAIL_CONTACTS, "self_info": {}})())
    box, users = mp.mail.Mailbox(), mp.mail.MailUsers()
    users.users = [{"key": k, "name": "", "added": 0} for k in approved]
    monkeypatch.setattr(mp.mail, "box", box)
    monkeypatch.setattr(mp.mail, "allowed_users", users)
    monkeypatch.setattr(mp.config, "ADMIN_PUBKEYS", set())
    return box


def test_mail_dm_only(monkeypatch):
    box = _mail_setup(monkeypatch)
    # On a channel, even from an approved contact's name: a name can be faked, a DM can't
    assert asyncio.run(mp.commands.run_command("!mail", "sam hi", "Bob", {}, ("chan", 1))) ==         "@[Bob] 📮 !mail only works in a DM to the bot"
    assert asyncio.run(mp.commands.run_command("!mail", "", "Bob", {}, ("chan", 1))) ==         "@[Bob] 📮 !mail only works in a DM to the bot"
    assert box.messages == []
    assert "Held for Bob" in asyncio.run(mp.commands.run_command("!mail", "bob hi", "", {}, ("dm", "5a5a5a5a5a5a")))


def test_mail_only_from_approved_users(monkeypatch):
    box = _mail_setup(monkeypatch, approved=("5a5a5a5a5a5a",))
    denied = "📮 !mail is only for approved users. Ask an admin to add you"
    # Bob is a contact but not approved
    assert asyncio.run(mp.commands.run_command("!mail", "sam hi", "", {}, ("dm", "b0b0b0b0b0b0"))) == denied
    # Not even a contact
    assert asyncio.run(mp.commands.run_command("!mail", "sam hi", "", {}, ("dm", "eeeeeeeeeeee"))) == denied
    assert box.messages == []
    # Admins can always use it
    monkeypatch.setattr(mp.config, "ADMIN_PUBKEYS", {"B0B0B0B0B0B0"})
    assert "Held for Sam" in asyncio.run(mp.commands.run_command("!mail", "sam hi", "", {}, ("dm", "b0b0b0b0b0b0")))


def test_mail_per_sender_cap(monkeypatch):
    monkeypatch.setattr(mp.config, "MAIL_MAX_PER_SENDER", 3)
    box = mp.mail.Mailbox()
    for to in ("aa0000000000", "bb0000000000", "cc0000000000"):
        assert box.add("Alice", "a1a1a1a1a1a1", to, to, "hi").startswith("📮 Held for")
    assert box.add("Alice", "a1a1a1a1a1a1", "Dee", "dd0000000000", "hi") ==         "📮 You have 3 messages waiting, the most allowed. Try again once some are delivered"
    # Other senders aren't affected
    assert box.add("Carol", "c3c3c3c3c3c3", "Dee", "dd0000000000", "hi").startswith("📮 Held for Dee (1/10)")
    # Once some are delivered, Alice can send again
    box.take_for("aa0000000000")
    assert box.add("Alice", "a1a1a1a1a1a1", "Dee", "dd0000000000", "hi").startswith("📮 Held for Dee")


def test_mail_take_for_limit():
    box = mp.mail.Mailbox()
    for i in range(5):
        box.add("Alice", f"s{i}", "Bob", "b0b0b0b0b0b0", f"m{i}", now=HEARD_NOW + i)
    # Room for 2 in the send queue: the 2 oldest go now, the rest wait for next time
    assert [m["text"] for m in box.take_for("b0b0b0b0b0b0", limit=2)] == ["m0", "m1"]
    assert [m["text"] for m in box.messages] == ["m2", "m3", "m4"]
    assert box.take_for("b0b0b0b0b0b0", limit=0) == [] and box.take_for("b0b0b0b0b0b0", limit=-3) == []
    assert [m["text"] for m in box.take_for("b0b0b0b0b0b0")] == ["m2", "m3", "m4"]


def test_sender_room():
    async def check():
        sender = mp.tx.Sender(None)
        assert sender.room() == 50
        sender.dm("b0b0", "hi")
        assert sender.room() == 49
    asyncio.run(check())


def test_mail_users_add_list_remove(tmp_path, monkeypatch):
    path = str(tmp_path / "authed_mail_users.json")
    users = mp.mail.MailUsers()
    assert mp.mail.list_mail_users(MAIL_CONTACTS, users) == "📮 No mail users. Add one with !addmailuser <public key>"
    assert mp.mail.add_mail_user("B0B0B0B0B0B00000", MAIL_CONTACTS, users, path) == "📮 Bob can now use !mail (1 user)"
    assert mp.mail.add_mail_user("b0b0b0b0b0b0", MAIL_CONTACTS, users, path) == "📮 b0b0b0b0b0b0 can already use !mail"
    assert mp.mail.add_mail_user("1c2d3e4f5a6b", MAIL_CONTACTS, users, path) == "📮 1c2d3e4f5a6b can now use !mail (2 users)"
    assert mp.mail.add_mail_user("b0b0", MAIL_CONTACTS, users, path).startswith("📮 Use !addmailuser")
    assert mp.mail.add_mail_user("not-a-key!!!", MAIL_CONTACTS, users, path).startswith("📮 Use !addmailuser")
    assert users.allowed("B0B0B0B0B0B0FFFF") and not users.allowed("b1b1b1b1b1b1")
    assert mp.mail.list_mail_users(MAIL_CONTACTS, users) == "📮 2 mail users: Bob b0b0b0b0b0b0, 1c2d3e4f5a6b"
    # Written straight away, and read back
    loaded = mp.mail.MailUsers()
    loaded.load(path)
    assert [u["key"] for u in loaded.users] == ["b0b0b0b0b0b00000", "1c2d3e4f5a6b"]
    assert mp.mail.remove_mail_user("ffff", users, path) == "📮 No mail user with key ffff"
    assert mp.mail.remove_mail_user("b0b0", users, path) == "📮 Bob can no longer use !mail"
    loaded.load(path)
    assert [u["key"] for u in loaded.users] == ["1c2d3e4f5a6b"]
    assert mp.mail.remove_mail_user("xyz", users, path).startswith("📮 Use !removemailuser")


def test_mail_users_ambiguous_remove_and_bad_file(tmp_path):
    users = mp.mail.MailUsers()
    users.users = [{"key": "abab00000000"}, {"key": "abab11111111"}]
    assert mp.mail.remove_mail_user("abab", users, str(tmp_path / "u.json")) == "📮 2 mail users start abab, give more of the key"
    (tmp_path / "bad.json").write_text('[{"key": "zz"}, {"key": 5}, "x", {"key": "abcdefabcdef"}]', encoding="utf-8")
    users.load(str(tmp_path / "bad.json"))
    assert [u["key"] for u in users.users] == ["abcdefabcdef"]


def test_mail_user_save_failure_rolls_back(tmp_path):
    users = mp.mail.MailUsers()
    bad = str(tmp_path / "missing_dir" / "u.json")
    assert mp.mail.add_mail_user("b0b0b0b0b0b0", MAIL_CONTACTS, users, bad) == "📮 Couldn't save the mail users file, see the log"
    assert users.users == []


def test_mail_user_commands_are_admin_only():
    for cmd in ("!addmailuser", "!removemailuser", "!listmailuser"):
        assert mp.commands.parse_command(cmd + " b0b0b0b0b0b0")[0] == cmd
        assert not mp.commands.command_allowed(cmd, admin=False) and mp.commands.command_allowed(cmd, admin=True)
    assert mp.commands.parse_command("!listmailusers")[0] == "!listmailuser"


def test_clear_mail():
    box = mp.mail.Mailbox()
    box.add("Alice", "a1a1a1a1a1a1", "Bob", "b0b0b0b0b0b0", "one")
    box.add("Alice", "a1a1a1a1a1a1", "Bob", "b0b0b0b0b0b0", "two")
    box.add("Alice", "a1a1a1a1a1a1", "Sam 🐬 Base", "5a5a5a5a5a5a", "three")
    box.add("Alice", "a1a1a1a1a1a1", "Bobby Two", "b1b1b1b1b1b1", "four")
    box.add("Carol", "c3c3c3c3c3c3", "Bob", "b0b0b0b0b0b0", "carol's")
    box.dirty = False
    assert mp.mail.clear_mail("zed", "a1a1a1a1a1a1", box) == \
        "📮 No messages waiting for 'zed'. Waiting: Bob 2, Sam 🐬 Base 1, Bobby Two 1"
    assert mp.mail.clear_mail("bo", "a1a1a1a1a1a1", box) == "📮 2 match 'bo': Bob, Bobby Two. Give more of the name"
    assert not box.dirty
    # Only Alice's own messages to Bob go, not Carol's
    assert mp.mail.clear_mail("bob", "a1a1a1a1a1a1", box) == "📮 Cleared 2 messages to Bob"
    assert [m["text"] for m in box.messages] == ["three", "four", "carol's"] and box.dirty
    assert mp.mail.clear_mail("sam base", "a1a1a1a1a1a1", box) == "📮 Cleared 1 message to Sam 🐬 Base"   # emoji optional
    assert mp.mail.clear_mail("b1b1", "a1a1a1a1a1a1", box) == "📮 Cleared 1 message to Bobby Two"        # start of key
    assert mp.mail.clear_mail("", "a1a1a1a1a1a1", box) == "📮 You have no messages waiting"
    assert mp.mail.clear_mail("", "c3c3c3c3c3c3", box) == "📮 Cleared 1 waiting message"
    assert box.messages == []


def test_clear_mail_command(monkeypatch):
    box = _mail_setup(monkeypatch, approved=())
    box.add("Bob", "b0b0b0b0b0b0", "Sam", "5a5a5a5a5a5a", "hi")
    assert mp.commands.parse_command("!clearmail sam") == ("!clearmail", "sam")
    assert mp.commands.parse_command("!mailclear")[0] == "!clearmail"
    assert asyncio.run(mp.commands.run_command("!clearmail", "", "Bob", {}, ("chan", 1))) == \
        "@[Bob] 📮 !clearmail only works in a DM to the bot"
    # Works from the sender's own key, even though Bob isn't an approved mail user
    assert asyncio.run(mp.commands.run_command("!clearmail", "", "", {}, ("dm", "b0b0b0b0b0b0"))) == \
        "📮 Cleared 1 waiting message"
    assert box.messages == []


def test_mail_command(monkeypatch):
    box = _mail_setup(monkeypatch)
    # The sender is known by the key of the DM, and named from contacts
    assert asyncio.run(mp.commands.run_command("!mail", "sam hi", "", {}, ("dm", "b0b0b0b0b0b0"))) ==         "📮 Held for Sam 🐬 Base (1/10), sent by DM when the bot next hears them"
    assert (box.messages[0]["from"], box.messages[0]["from_id"]) == ("Bob", "b0b0b0b0b0b0")


def test_save_is_admin_only():
    assert mp.commands.parse_command("!save") == ("!save", "")
    assert not mp.commands.command_allowed("!save", admin=False)
    assert mp.commands.command_allowed("!save", admin=True)


def test_who_and_status_commands(monkeypatch):
    monkeypatch.setattr(mp.state, "radio", type("Radio", (), {"contacts": {}, "self_info": {}})())
    h = mp.heard.Heard()
    h.record("Alice", "advert", "a1a1", {"path_len": 1})
    monkeypatch.setattr(mp.heard, "log", h)
    assert asyncio.run(mp.commands.run_command("!who", "", "Bob", {})) == "@[Bob] 👥 1 heard in 24h: Alice 0s"
    assert asyncio.run(mp.commands.run_command("!status", "alice", "Bob", {})) ==         "@[Bob] 👤 Alice: heard 0s ago by advert, 1 hop"


# ---------- !freq ----------
def test_format_freq(monkeypatch):
    assert mp.tools.format_freq("pmr").startswith("📻 PMR446 MHz: 1 446.00625")
    assert mp.tools.format_freq("PMR446") == mp.tools.format_freq("pmr")
    assert mp.tools.format_freq("") == "📻 Frequencies: !freq pmr, cb, ham, hf, marine, air, mesh"
    assert mp.tools.format_freq("nonsense") == mp.tools.format_freq("")
    info = {"radio_freq": 869.618, "radio_bw": 62.5, "radio_sf": 8, "radio_cr": 8}
    assert mp.tools.format_freq("mesh", info) == "📻 MeshCore here: 869.618 MHz, BW 62.5kHz, SF8, CR8"
    assert mp.tools.format_freq("lora", {}) == "📻 MeshCore radio settings not known"
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert mp.tools.format_freq("air") == "Air band: 121.500 MHz distress (guard)"


def test_freq_lists_fit_one_message():
    for topic in mp.config.FREQ_LISTS:
        text = mp.tools.format_freq(topic)
        assert len(text.encode("utf-8")) <= mp.config.MAX_REPLY_BYTES - len("@[Somebody] "), topic


@pytest.mark.skipif(mp.config.tomllib is None, reason="needs Python 3.11+")
def test_freq_lists_from_config(tmp_path, restore_settings):
    path = tmp_path / "config.toml"
    path.write_text('[freq_lists]\nlocal = "GB3XX 145.7250"\nair = ""\n', encoding="utf-8")
    mp.config.load_config(str(path))
    assert mp.config.FREQ_LISTS["local"] == "GB3XX 145.7250"
    assert "air" not in mp.config.FREQ_LISTS and "pmr" in mp.config.FREQ_LISTS


def test_format_dist_without_positions(monkeypatch):
    assert mp.geo.format_dist({"path_len": 1, "path_nodes": ["ee"]}, {}, None, None) == \
        "📏 1 hop, no positions known along the path"
    assert mp.geo.format_dist({"path_len": 0}, {}, CROMER, NORWICH) == "📏 Heard directly, you are 34km away"
    assert mp.geo.format_dist({"direct": True}, {}, None, NORWICH) == "📏 Direct route, your position isn't known"
    assert mp.geo.format_dist({"path_len": 2}, {}, None, NORWICH) == "📏 2 hops, path not reported"
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert mp.geo.format_dist({"path_len": 1, "path_nodes": ["c3"]}, GPS_CONTACTS, None, NORWICH) == \
        "Dist: You >?> c3 >5.0km> Bot | 5.0km+, 1 of 2 legs unknown"


def test_dist_command(monkeypatch):
    radio = type("Radio", (), {"contacts": GPS_CONTACTS, "self_info": {"adv_lat": NORWICH[0], "adv_lon": NORWICH[1]}})()
    monkeypatch.setattr(mp.state, "radio", radio)
    info = {"path_len": 1, "path_nodes": ["a1"]}
    assert mp.commands.parse_command("!dist") == ("!dist", "")
    # Channel: the sender is found by name
    assert asyncio.run(mp.commands.run_command("!dist", "", "Alice", info, ("chan", 1))) == \
        "@[Alice] 📏 You ›15km› a1 ›19km› Bot | 34km total | 34km direct"
    # DM: the sender is found by key
    assert asyncio.run(mp.commands.run_command("!dist", "", "", info, ("dm", "d4dd00"))).startswith("📏 You ›15km› a1")


def test_help_fits_one_message(monkeypatch):
    monkeypatch.setattr(mp.config, "MET_OFFICE_API_KEY", "key")
    for topic in ("",) + mp.commands.HELP_TOPICS:
        assert len(mp.commands.help_text(topic).encode("utf-8")) <= mp.config.MAX_REPLY_BYTES
    for topic in mp.commands.HELP_TOPICS:
        assert f"!help{topic}" in mp.commands.help_text()


def test_help_topics(monkeypatch):
    monkeypatch.setattr(mp.config, "MET_OFFICE_API_KEY", "key")
    assert "!path" in mp.commands.help_text("test")
    assert "!roll" in mp.commands.help_text("fun")
    for cmd in ("!conv", "!ohm", "!res"):
        assert cmd in mp.commands.help_text("conv")
    assert "!hf" in mp.commands.help_text("radio")
    for cmd in ("!who", "!status", "!bearing", "!freq", "!mail"):
        assert cmd in mp.commands.help_text("net")
    # !helpwx and !help wx give the same reply, an unknown topic gives the topic list
    assert asyncio.run(mp.commands.run_command("!helpwx", "", "Alice", {})) == mp.commands.help_text("wx")
    assert asyncio.run(mp.commands.run_command("!help", "wx", "Alice", {})) == mp.commands.help_text("wx")
    assert asyncio.run(mp.commands.run_command("!help", "nonsense", "Alice", {})) == mp.commands.help_text()


@pytest.mark.parametrize("arg, expected", [
    ("10 mi km", "📐 10 mi = 16.09 km"),
    ("10mi to km", "📐 10 mi = 16.09 km"),
    ("5 ft in", "📐 5 ft = 60 in"),                  # "in" after a unit is inches
    ("5 ft in m", "📐 5 ft = 1.524 m"),
    ("1,000 m km", "📐 1000 m = 1 km"),
    ("20 c", "📐 20°C = 68°F"),                     # one unit uses its usual partner
    ("20 °C °F", "📐 20°C = 68°F"),
    ("-40 f c", "📐 -40°F = -40°C"),
    ("300 k", "📐 300 K = 26.85°C"),
    ("3 kg lb", "📐 3 kg = 6.614 lb"),
    ("5 st", "📐 5 st = 31.75 kg"),
    ("1 fl oz ml", "📐 1 fl oz = 28.41 ml"),
    ("50 km/h mph", "📐 50 km/h = 31.07 mph"),
    ("1013 hpa", "📐 1013 hPa = 29.91 inHg"),
    ("5 w dbm", "📐 5 W = 36.99 dBm"),
    ("20 dbm", "📐 20 dBm = 0.1 W"),
    ("868 mhz", "📐 868 MHz = 34.54 cm"),          # wavelength
    ("14.2 mhz m", "📐 14.2 MHz = 21.11 m"),
    ("2 m mhz", "📐 2 m = 149.9 MHz"),
])
def test_convert_units(arg, expected):
    assert mp.tools.convert_units(arg) == expected


def test_convert_units_errors(monkeypatch):
    assert mp.tools.convert_units("").startswith("Use !conv")
    assert mp.tools.convert_units("ten mi km").startswith("Use !conv")
    assert mp.tools.convert_units("10 foo").startswith("Unknown unit 'foo'")
    assert mp.tools.convert_units("10 km kg") == "Can't convert km to kg"
    assert mp.tools.convert_units("-300 c") == "That's below absolute zero"
    assert mp.tools.convert_units("0 w dbm").startswith("Can't convert")
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert asyncio.run(mp.commands.run_command("!conv", "10 mi km", "Alice", {})) == "@[Alice] 10 mi = 16.09 km"


@pytest.mark.parametrize("arg, expected", [
    ("12v 2a", "⚡ 12 V, 2 A → 6 Ω, 24 W"),                       # V and I
    ("12 V 2 A", "⚡ 12 V, 2 A → 6 Ω, 24 W"),
    ("5v 220r", "⚡ 5 V, 220 Ω → 22.73 mA, 113.6 mW"),            # V and R
    ("100w 13.8v", "⚡ 13.8 V, 100 W → 7.246 A, 1.904 Ω"),        # V and P
    ("4.7k 20ma", "⚡ 20 mA, 4.7 kΩ → 94 V, 1.88 W"),             # I and R, bare k is ohms
    ("2a 50w", "⚡ 2 A, 50 W → 25 V, 12.5 Ω"),                    # I and P
    ("10w 50ohm", "⚡ 50 Ω, 10 W → 22.36 V, 447.2 mA"),           # R and P
    ("1M 10v", "⚡ 10 V, 1 MΩ → 10 µA, 100 µW"),                  # M is mega, m is milli
    ("9v 1kΩ", "⚡ 9 V, 1 kΩ → 9 mA, 81 mW"),
    ("5 volts 100 mA", "⚡ 5 V, 100 mA → 50 Ω, 500 mW"),
])
def test_ohms_law(arg, expected):
    assert mp.tools.ohms_law(arg) == expected


@pytest.mark.parametrize("arg, expected", [
    ("yellow violet red gold", "🟨🟪🟥🥇 Yellow Violet Red Gold = 4.7 kΩ ±5%"),
    ("gold red violet yellow", "🟨🟪🟥🥇 Yellow Violet Red Gold = 4.7 kΩ ±5%"),      # read from the wrong end
    ("bn bk rd gd", "🟫⬛🟥🥇 Brown Black Red Gold = 1 kΩ ±5%"),
    ("brown, black, red", "🟫⬛🟥 Brown Black Red = 1 kΩ ±20%"),                   # 3 bands
    ("brown black black brown brown", "🟫⬛⬛🟫🟫 Brown Black Black Brown Brown = 1 kΩ ±1%"),
    ("brown black black brown brown red", "🟫⬛⬛🟫🟫🟥 Brown Black Black Brown Brown Red = 1 kΩ ±1% 50ppm/K"),
    ("purple green silver gold", "🟪🟩🥈🥇 Violet Green Silver Gold = 0.75 Ω ±5%"),
    ("black", "⬛ Black = 0 Ω zero-ohm link"),
    ("4k7", "4.7 kΩ: 🟨🟪🟥🥇 Yellow Violet Red Gold ±5% | 🟨🟪⬛🟫🟫 Yellow Violet Black Brown Brown ±1%"),
    ("4.7 k", "4.7 kΩ: 🟨🟪🟥🥇 Yellow Violet Red Gold ±5% | 🟨🟪⬛🟫🟫 Yellow Violet Black Brown Brown ±1%"),
    ("4r7", "4.7 Ω: 🟨🟪🥇🥇 Yellow Violet Gold Gold ±5% | 🟨🟪⬛🥈🟫 Yellow Violet Black Silver Brown ±1%"),
    ("220 ohm", "220 Ω: 🟥🟥🟫🥇 Red Red Brown Gold ±5% | 🟥🟥⬛⬛🟫 Red Red Black Black Brown ±1%"),
    ("10k 1%", "10 kΩ: 🟫⬛🟧🟫 Brown Black Orange Brown ±1% | 🟫⬛⬛🟥🟫 Brown Black Black Red Brown ±1%"),
    ("1M", "1 MΩ: 🟫⬛🟩🥇 Brown Black Green Gold ±5% | 🟫⬛⬛🟨🟫 Brown Black Black Yellow Brown ±1%"),
    ("4.99k", "4.99 kΩ: 🟨⬜⬜🟫🟫 Yellow White White Brown Brown ±1%"),              # needs 3 figures
    ("0.47", "0.47 Ω: 🟨🟪🥈🥇 Yellow Violet Silver Gold ±5%"),
    ("0", "0 Ω: ⬛ Black (zero-ohm link)"),
])
def test_resistor(arg, expected):
    assert mp.tools.resistor(arg) == expected


def test_resistor_errors(monkeypatch):
    for arg in ("", "hello", "4.7x"):
        assert mp.tools.resistor(arg).startswith("Use !res")
    assert mp.tools.resistor("red red pink") == "Unknown colour 'pink'. See !helpconv"
    assert mp.tools.resistor("red red") == "Give 3 to 6 bands, e.g. !res brown black red gold"
    assert mp.tools.resistor("gold gold red gold") == "Gold Gold: digit bands can't be gold or silver"
    assert mp.tools.resistor("red red white white") == "White isn't a tolerance band"
    assert mp.tools.resistor("10k 3%") == "No band for ±3%. Try 1%, 2%, 5% or 10%"
    assert mp.tools.resistor("4991").endswith("has no colour code (0.1 Ω to 999 GΩ, 3 figures)")
    assert mp.tools.resistor("0.05").startswith("0.05 Ω has no colour code")
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert asyncio.run(mp.commands.run_command("!res", "brown black red gold", "Alice", {})) == \
        "@[Alice] Brown Black Red Gold = 1 kΩ ±5%"
    assert mp.tools.resistor("4k7") == "4.7 kΩ: 4-band Yellow Violet Red Gold ±5% | 5-band Yellow Violet Black Brown Brown ±1%"


def test_resistor_drops_names_to_fit():
    full = "1.5 MΩ: 🟫🟩🟩🥈 Brown Green Green Silver ±10% | 🟫🟩⬛🟨🥈 Brown Green Black Yellow Silver ±10%"
    assert mp.tools.resistor("1.5M 10%") == full
    assert mp.tools.resistor("1.5M 10%", budget=100) == "1.5 MΩ: 🟫🟩🟩🥈 ±10% | 🟫🟩⬛🟨🥈 ±10%"


def test_ohms_law_errors(monkeypatch):
    for arg in ("", "12v", "hello", "12v and 2a", "1v 2a 3r"):
        assert mp.tools.ohms_law(arg).startswith("Use !ohm")
    assert mp.tools.ohms_law("12v 3v") == "Give two different values, such as volts and amps"
    assert mp.tools.ohms_law("12v 0a") == "Values must be above 0"
    assert mp.tools.ohms_law("12v 2x") == "Unknown unit 'x'. See !helpconv"
    assert mp.tools.ohms_law("12 2a") == "Give each value a unit: V, A, Ω or W"
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert asyncio.run(mp.commands.run_command("!ohm", "12v 2a", "Alice", {})) == "@[Alice] 12 V, 2 A -> 6 Ω, 24 W"


# ---------- commands ----------
def test_ping_shows_only_hops(monkeypatch):
    info = {"path_len": 2, "path_nodes": ["a1", "b2"], "snr": 7.5, "rssi": -85}
    assert asyncio.run(mp.commands.run_command("ping", "", "Alice", info)) == "@[Alice] 🏓 Pong (2 hops)"
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert asyncio.run(mp.commands.run_command("ping", "", "Alice", info)) == "@[Alice] Pong (2 hops)"


def test_test_shows_rx_report(monkeypatch):
    info = {"path_len": 2, "path_nodes": ["a1", "b2"], "snr": 7.5, "rssi": -85}
    monkeypatch.setattr(mp.state, "radio", None)
    monkeypatch.setattr(mp.config, "LOCATIONS", {"Norwich": NORWICH})
    monkeypatch.setattr(mp.config, "DEFAULT_LOCATION", "Norwich")
    # Sender's position unknown: no distance
    assert asyncio.run(mp.commands.run_command("test", "", "Bob", info, ("chan", 1))) == \
        "@[Bob] 📡 RX in Norwich | 🐸 (2 hops)"
    # Sender advertises a position (Cromer), bot at Norwich
    monkeypatch.setattr(mp.state, "radio", type("Radio", (), {"contacts": GPS_CONTACTS, "self_info": {}})())
    assert asyncio.run(mp.commands.run_command("test", "", "Alice", info, ("chan", 1))) == \
        "@[Alice] 📡 RX in Norwich | 🐸 (2 hops) | 📏 34km"
    assert asyncio.run(mp.commands.run_command("test", "", "", info, ("dm", "d4dd00"))) == \
        "📡 RX in Norwich | 🐸 (2 hops) | 📏 34km"
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert asyncio.run(mp.commands.run_command("test", "", "Alice", info, ("chan", 1))) == \
        "@[Alice] RX in Norwich | (2 hops) | 34km"
    monkeypatch.setattr(mp.config, "DEFAULT_LOCATION", "")
    assert asyncio.run(mp.commands.run_command("test", "", "", info)) == "Test OK | (2 hops)"


def test_wx_unknown_place_is_silent(monkeypatch):
    def not_found(query):
        raise mp.geo.LocationError(f"'{query}' not found")
    monkeypatch.setattr(mp.config, "MET_OFFICE_API_KEY", "key")
    monkeypatch.setattr(mp.geo, "_geocode_sync", not_found)
    assert asyncio.run(mp.commands.run_command("!wx", "nowhere", "Alice", {})) is None
    # Scheduled messages and the CLI still get the error text
    assert asyncio.run(mp.wx.get_weather("nowhere")) == "WX: 'nowhere' not found"


def test_place_lookup_prefers_bigger_exact_match(monkeypatch):
    results = [
        {"name_1": "Brighton", "local_type": "Hamlet", "latitude": 50.3, "longitude": -4.9},
        {"name_1": "New Brighton", "local_type": "Town", "latitude": 53.4, "longitude": -3.0},
        {"name_1": "Brighton", "local_type": "Other Settlement", "latitude": 50.8, "longitude": -0.1},
        {"name_1": "Casnewydd", "name_2": "Newport", "local_type": "City", "latitude": 51.6, "longitude": -3.0},
    ]
    assert mp.geo._best_place(results, "brighton")["latitude"] == 50.8
    assert mp.geo._best_place(results, "Nowhere")["latitude"] == 50.3        # no exact match: first result
    monkeypatch.setattr(mp.geo, "geocode_cache", mp.net.TTLCache(60))
    monkeypatch.setattr(mp.net, "get_json", lambda url, headers=None: {"result": results})
    assert mp.geo._geocode_sync("Newport") == (51.6, -3.0, "Newport")


# ---------- rate limiting ----------
def test_rate_limiter_per_user(monkeypatch):
    monkeypatch.setattr(mp.config, "RATE_LIMIT_PER_USER", (2, 60))
    limiter = mp.ratelimit.RateLimiter()
    assert limiter.check("u1", "ch:1")[0]
    assert limiter.check("u1", "ch:1")[0]
    ok, bucket, retry = limiter.check("u1", "ch:1")
    assert not ok and bucket == "user" and 1 <= retry <= 61
    assert limiter.check("u2", "ch:1")[0]
    assert limiter.should_notify("u1")
    assert not limiter.should_notify("u1")


# ---------- Met Office call budget ----------
def test_call_budget(monkeypatch):
    monkeypatch.setattr(mp.config, "WX_DAILY_CALL_BUDGET", 2)
    budget = mp.wx.CallBudget()
    assert budget.take() and budget.take()
    assert not budget.take()
    budget._day = budget._day - timedelta(days=1)     # a new UTC day resets the count
    assert budget.take()


def test_fetch_uses_cache_and_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(mp.config, "MET_OFFICE_API_KEY", "key")
    monkeypatch.setattr(mp.wx, "_wx_cache", mp.net.TTLCache(60))
    monkeypatch.setattr(mp.wx, "daily_calls", mp.wx.CallBudget())
    monkeypatch.setattr(mp.config, "WX_DAILY_CALL_BUDGET", 1)
    monkeypatch.setattr(mp.net, "get_json", lambda url, headers=None: calls.append(url) or {"ok": 1})
    assert mp.wx._fetch_metoffice_sync("hourly", 52.6, 1.3) == {"ok": 1}
    assert mp.wx._fetch_metoffice_sync("hourly", 52.6, 1.3) == {"ok": 1}     # cached
    assert len(calls) == 1
    with pytest.raises(mp.wx.QuotaError):
        mp.wx._fetch_metoffice_sync("hourly", 50.0, -1.0)


def test_quota_reply(monkeypatch):
    monkeypatch.setattr(mp.config, "MET_OFFICE_API_KEY", "key")
    monkeypatch.setattr(mp.geo, "_geocode_sync", lambda q: (52.6, 1.3, "Norwich"))

    def over(kind, lat, lon):
        raise mp.wx.QuotaError("300 calls used today")
    monkeypatch.setattr(mp.wx, "_fetch_metoffice_sync", over)
    assert asyncio.run(mp.wx.get_weather("")) == "WX: daily quota used, try tomorrow"


def test_no_api_key_skips_weather(monkeypatch):
    monkeypatch.setattr(mp.config, "MET_OFFICE_API_KEY", "")
    monkeypatch.setattr(mp.geo, "_geocode_sync", lambda q: pytest.fail("should not look up the place"))
    for cmd in mp.commands.WX_COMMANDS:
        assert not mp.commands.command_allowed(cmd, admin=False)
        assert not mp.commands.command_allowed(cmd, admin=True)          # admins too
        assert asyncio.run(mp.commands.run_command(cmd, "Cromer", "Alice", {})) is None
    assert mp.commands.command_allowed("!warn", admin=False)
    assert asyncio.run(mp.wx.get_weather("Cromer")) is None
    assert asyncio.run(mp.reports.expand_tokens("Morning {wx} {wxf:Cromer}")).strip() == "Morning"
    help_text = mp.commands.help_text("wx")
    assert "!wx" not in help_text and "Weather: !warn" in help_text
    monkeypatch.setattr(mp.config, "MET_OFFICE_API_KEY", "key")
    assert "Weather: !wx now, !wxh hourly, !wxf 3-day, !warn" in mp.commands.help_text("wx")
    assert mp.commands.command_allowed("!wx", admin=False)


def test_schedule_skips_empty_message(monkeypatch):
    sent = []
    fake = type("FakeSender", (), {"channel": lambda self, ch, t: sent.append((ch, t)),
                                   "dm": lambda self, k, t: sent.append((k, t))})()
    monkeypatch.setattr(mp.config, "MET_OFFICE_API_KEY", "")
    monkeypatch.setattr(mp.state, "muted_until", 0.0)
    monkeypatch.setattr(mp.schedule, "due_slot", lambda entry, now: "slot-1")

    async def run_once():
        task = asyncio.create_task(mp.schedule.scheduler(fake, [{"name": "x", "time": "07:30", "channel": 1, "text": "{wx}"}]))
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(run_once())
    assert sent == []


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
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    out = mp.wx.format_hourly(_hourly(), "Norwich")
    assert "Cloudy" in out and "13C feels 12C" in out
    assert "Wind NW 6mph gust 15" in out


def test_format_hours_respects_budget():
    out = mp.wx.format_hours(_hourly(), "Norwich", hours=6, step=1, budget=80)
    assert len(out.encode("utf-8")) <= 80
    assert out.count("h ") >= 1


# ---------- scheduler ----------
def test_due_slot_daily():
    entry = {"time": "07:30", "days": ["mon"]}
    monday = datetime(2026, 9, 21, 7, 31, tzinfo=mp.config.TIMEZONE)
    assert mp.schedule.due_slot(entry, monday) == "time:2026-09-21 07:30"
    assert mp.schedule.due_slot(entry, monday + timedelta(minutes=5)) is None
    assert mp.schedule.due_slot(entry, monday + timedelta(days=1)) is None


def test_due_slot_every_minutes():
    entry = {"every_minutes": 360, "start": "00:00"}
    now = datetime(2026, 9, 21, 12, 1, tzinfo=mp.config.TIMEZONE)
    assert mp.schedule.due_slot(entry, now) == "every:2026-09-21 12:00"
    assert mp.schedule.due_slot(entry, now + timedelta(minutes=30)) is None


def test_due_slot_at_once():
    entry = {"at": "2026-12-25 09:00"}
    assert mp.schedule.due_slot(entry, datetime(2026, 12, 25, 9, 1, tzinfo=mp.config.TIMEZONE)) == "at:2026-12-25 09:00"
    assert mp.schedule.due_slot(entry, datetime(2027, 12, 25, 9, 1, tzinfo=mp.config.TIMEZONE)) is None


def test_due_slot_at_yearly():
    entry = {"at": "12-25 09:00"}
    assert mp.schedule.due_slot(entry, datetime(2026, 12, 25, 9, 1, tzinfo=mp.config.TIMEZONE)) == "at:2026-12-25 09:00"
    assert mp.schedule.due_slot(entry, datetime(2027, 12, 25, 9, 1, tzinfo=mp.config.TIMEZONE)) == "at:2027-12-25 09:00"
    assert mp.schedule.due_slot(entry, datetime(2026, 12, 24, 9, 1, tzinfo=mp.config.TIMEZONE)) is None
    leap = {"at": "02-29 09:00"}
    assert mp.schedule.due_slot(leap, datetime(2027, 3, 1, 9, 1, tzinfo=mp.config.TIMEZONE)) is None
    assert mp.schedule.due_slot(leap, datetime(2028, 2, 29, 9, 1, tzinfo=mp.config.TIMEZONE)) == "at:2028-02-29 09:00"


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
    assert [e["name"] for e in mp.schedule.validate_schedule(entries)] == ["ok", "yearly"]


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
    monkeypatch.setattr(mp.config, "TIMEZONE", LONDON)
    now = datetime(2026, 9, 24, 14, tzinfo=LONDON)
    warnings = mp.alerts.parse_warnings_feed(WARN_FEED_XML, now)
    assert [(w["level"], w["hazard"]) for w in warnings] == [("yellow", "rain"), ("amber", "wind"), ("yellow", "fog")]
    assert warnings[0]["start"] == datetime(2026, 9, 26, 6, tzinfo=LONDON)
    assert warnings[0]["end"] == datetime(2026, 9, 26, 21, tzinfo=LONDON)


def test_warning_year_rolls_over(monkeypatch):
    monkeypatch.setattr(mp.config, "TIMEZONE", LONDON)
    t = mp.alerts._warn_time("06", "00", "02", "Jan", datetime(2026, 12, 30, tzinfo=LONDON))
    assert t.year == 2027


def test_format_warnings(monkeypatch):
    monkeypatch.setattr(mp.config, "TIMEZONE", LONDON)
    now = datetime(2026, 9, 24, 14, tzinfo=LONDON)
    warnings = mp.alerts.parse_warnings_feed(WARN_FEED_XML, now)
    out = mp.alerts.format_warnings(warnings, "ee", now=now)
    # Amber first, expired fog warning left out
    assert out == "⚠️ East of England: 🟠💨 Wind Thu 18:00-Fri 12:00 | 🟡🌧️ Rain Sat 06:00-21:00"
    short = mp.alerts.format_warnings(warnings, "ee", budget=70, now=now)
    assert short.endswith("+1 more") and len(short.encode("utf-8")) <= 70
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert mp.alerts.format_warnings([], "ee", now=now) == "No weather warnings for East of England"


@pytest.mark.parametrize("postcode, code", [
    ({"country": "England", "region": "London"}, "se"),
    ({"country": "England", "region": "East of England"}, "ee"),
    ({"country": "Scotland", "region": None, "admin_district": "Glasgow City"}, "st"),
    ({"country": "Wales", "region": None}, "wl"),
    ({"country": "Isle of Man"}, "uk"),
])
def test_warn_region_from_postcode(monkeypatch, postcode, code):
    monkeypatch.setattr(mp.geo, "geocode_cache", mp.net.TTLCache(60))
    monkeypatch.setattr(mp.geo, "_geocode_sync", lambda q: (51.5, -0.1, "Somewhere"))
    monkeypatch.setattr(mp.net, "get_json", lambda url, headers=None: {"result": [postcode]})
    assert mp.alerts._warn_region_sync("Somewhere") == code


def test_warn_region_code_needs_no_lookup(monkeypatch):
    monkeypatch.setattr(mp.geo, "_geocode_sync", lambda q: pytest.fail("should not geocode"))
    assert mp.alerts._warn_region_sync("NW") == "nw"
    assert mp.alerts._warn_region_sync("uk") == "uk"


def test_warn_unknown_place_is_silent(monkeypatch):
    def not_found(query):
        raise mp.geo.LocationError(f"'{query}' not found")
    monkeypatch.setattr(mp.geo, "_geocode_sync", not_found)
    assert asyncio.run(mp.commands.run_command("!warn", "nowhere", "Alice", {})) is None


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
    monkeypatch.setattr(mp.config, "TIMEZONE", LONDON)
    monkeypatch.setattr(mp.state, "muted_until", 0.0)
    monkeypatch.setattr(mp.alerts, "watcher", mp.alerts.WarnWatcher())
    feed = {"ee": []}
    monkeypatch.setattr(mp.alerts, "_fetch_warnings_sync", lambda code: feed[code])
    return feed


NOW = datetime(2026, 9, 24, 14, tzinfo=LONDON)
RAIN = _warning("yellow", "rain", NOW + timedelta(hours=16), NOW + timedelta(hours=31))
WIND = _warning("amber", "wind", NOW + timedelta(hours=4), NOW + timedelta(hours=22))


def test_warn_starts_watch_on_its_channel(warn_feed, monkeypatch):
    monkeypatch.setattr(mp.alerts, "_warn_region_sync", lambda q: "ee")
    warn_feed["ee"] = [RAIN]
    reply = asyncio.run(mp.commands.run_command("!warn", "", "Alice", {}, ("chan", 1)))
    assert reply.startswith("@[Alice] ⚠️ East of England:")
    assert list(mp.alerts.watcher.watches) == [("chan", 1, "ee")]
    # No target (the CLI or a schedule): no watch
    monkeypatch.setattr(mp.alerts, "watcher", mp.alerts.WarnWatcher())
    asyncio.run(mp.commands.run_command("!warn", "", "Alice", {}))
    assert not mp.alerts.watcher.watches


def test_watch_posts_only_on_change(warn_feed):
    watcher, out = mp.alerts.watcher, _FakeSender()
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
    watcher, out = mp.alerts.watcher, _FakeSender()
    watcher.add("dm", "a1b2c3d4e5f6", "ee", [WIND], now=NOW)
    warn_feed["ee"] = [{**WIND, "level": "red"}]
    asyncio.run(watcher.check(out, now=NOW))
    assert out.sent[0][0] == "a1b2c3d4e5f6" and "🔴💨 Wind" in out.sent[0][1]


def test_watch_ignores_warning_running_out(warn_feed):
    watcher, out = mp.alerts.watcher, _FakeSender()
    watcher.add("chan", 1, "ee", [WIND, RAIN], now=NOW)
    warn_feed["ee"] = [WIND, RAIN]
    later = NOW + timedelta(hours=23)                       # wind has ended, rain still on
    asyncio.run(watcher.check(out, now=later))
    warn_feed["ee"] = [RAIN]                                # feed drops the ended warning
    asyncio.run(watcher.check(out, now=later))
    assert out.sent == []


def test_watch_waits_for_mute_then_expires(warn_feed, monkeypatch):
    watcher, out = mp.alerts.watcher, _FakeSender()
    watcher.add("chan", 1, "ee", [], now=NOW)
    warn_feed["ee"] = [RAIN]
    monkeypatch.setattr(mp.state, "muted_until", time.monotonic() + 600)
    asyncio.run(watcher.check(out, now=NOW))
    assert out.sent == []
    monkeypatch.setattr(mp.state, "muted_until", 0.0)
    asyncio.run(watcher.check(out, now=NOW))                # the change is posted after the mute
    assert len(out.sent) == 1
    watcher.watches[("chan", 1, "ee")]["until"] = time.monotonic() - 1
    asyncio.run(watcher.check(out, now=NOW))
    assert not watcher.watches


def test_watch_limit(warn_feed, monkeypatch):
    monkeypatch.setattr(mp.config, "WARN_WATCH_MAX", 1)
    watcher = mp.alerts.watcher
    watcher.add("chan", 1, "ee", [], now=NOW)
    watcher.add("chan", 3, "ee", [], now=NOW)
    assert list(watcher.watches) == [("chan", 1, "ee")]
    watcher.add("chan", 1, "ee", [RAIN], now=NOW)           # renewing an existing watch still works
    assert len(watcher.watches[("chan", 1, "ee")]["seen"]) == 1


# ---------- sun and moon ----------
def test_sun_times_london_midsummer(monkeypatch):
    monkeypatch.setattr(mp.config, "TIMEZONE", LONDON)
    rise, set_, state = mp.astro.sun_times(51.5074, -0.1278, date(2026, 6, 21))
    assert state == ""
    # Published times: 04:43 and 21:21 BST
    assert abs(rise - datetime(2026, 6, 21, 4, 43, tzinfo=LONDON)) < timedelta(minutes=2)
    assert abs(set_ - datetime(2026, 6, 21, 21, 21, tzinfo=LONDON)) < timedelta(minutes=2)


def test_sun_polar_day():
    assert mp.astro.sun_times(78.2, 15.6, date(2026, 6, 21))[2] == "up"
    assert mp.astro.sun_times(78.2, 15.6, date(2026, 12, 21))[2] == "down"


def test_format_sun(monkeypatch):
    monkeypatch.setattr(mp.config, "TIMEZONE", LONDON)
    out = mp.astro.format_sun(51.5074, -0.1278, "London", date(2026, 6, 21))
    assert out.startswith("London Sun 21 Jun 🌅 04:4") and "🌇 21:2" in out and "daylight" in out
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    assert "sunrise 04:4" in mp.astro.format_sun(51.5074, -0.1278, "London", date(2026, 6, 21))


def test_next_full_moon():
    # Published full moon: 26 Sep 2026 16:49 UTC
    full = mp.astro.next_moon_phase(datetime(2026, 9, 20, tzinfo=timezone.utc), 180)
    assert abs(full - datetime(2026, 9, 26, 16, 49, tzinfo=timezone.utc)) < timedelta(hours=1)


@pytest.mark.parametrize("when, emoji", [
    (datetime(2026, 9, 26, 17, tzinfo=timezone.utc), "🌕"),     # full
    (datetime(2026, 9, 23, 12, tzinfo=timezone.utc), "🌔"),     # waxing gibbous
    (datetime(2026, 9, 30, 12, tzinfo=timezone.utc), "🌖"),     # waning gibbous
    (datetime(2026, 10, 10, 16, tzinfo=timezone.utc), "🌑"),    # new
    (datetime(2026, 10, 14, 12, tzinfo=timezone.utc), "🌒"),    # waxing crescent
])
def test_moon_phase_emoji(when, emoji):
    idx, lit = mp.astro.moon_phase(when)
    assert mp.astro.MOON_PHASES[idx][0] == emoji
    assert 0 <= lit <= 100


def test_format_moon(monkeypatch):
    monkeypatch.setattr(mp.config, "TIMEZONE", LONDON)
    out = mp.astro.format_moon(datetime(2026, 9, 24, 12, tzinfo=LONDON))
    assert out.startswith("🌔 Waxing gibbous") and "🌕 Full Sat 26 Sep" in out


# ---------- stats and uptime ----------
def test_duration():
    assert mp.text.duration(42) == "42s"
    assert mp.text.duration(125) == "2m"
    assert mp.text.duration(3 * 3600 + 60) == "3h 1m"
    assert mp.text.duration(2 * 86400 + 3600 + 120) == "2d 1h 2m"


def test_format_stats(monkeypatch):
    stats = mp.stats.Stats()
    stats.commands = Counter({"!wx": 5, "ping": 3, "test": 1, "!roll": 1})
    stats.heard, stats.sent, stats.limited = 40, 9, 2
    monkeypatch.setattr(mp.stats, "counters", stats)
    out = mp.stats.format_stats(battery_mv=4020)
    assert out.startswith("📊 Cmds 10 (wx 5, ping 3,")
    assert "Heard 40" in out and "Limited 2" in out and "🔋4.02V" in out
    assert mp.stats.format_stats(budget=70).startswith("📊 Cmds 10 (wx 5) |")   # list shortened to fit
    short = mp.stats.format_stats(budget=60)
    assert len(short.encode("utf-8")) <= 60 and "(" not in short


def test_stats_and_uptime_are_admin_only(monkeypatch):
    monkeypatch.setattr(mp.config, "MET_OFFICE_API_KEY", "key")
    for cmd in ("!stats", "!uptime", "!mute", "!unmute", "!say"):
        assert not mp.commands.command_allowed(cmd, admin=False)
        assert mp.commands.command_allowed(cmd, admin=True)
    assert mp.commands.command_allowed("!wx", admin=False)
    for topic in ("",) + mp.commands.HELP_TOPICS:
        assert "!stats" not in mp.commands.help_text(topic) and "!uptime" not in mp.commands.help_text(topic)


def test_mute_and_unmute(monkeypatch):
    monkeypatch.setattr(mp.state, "muted_until", 0.0)
    assert mp.commands.mute("") == "🔊 Not muted"
    assert mp.commands.mute("30").startswith("🔇 Muted for 30m, until ")
    assert 29 * 60 < mp.state.mute_remaining() <= 30 * 60
    status = mp.commands.mute("")
    assert status.startswith(("🔇 Muted, 29m left", "🔇 Muted, 30m left")) and "(until " in status
    assert mp.commands.unmute() == "🔊 Unmuted"
    assert mp.state.mute_remaining() == 0
    mp.commands.mute("90")
    assert mp.commands.mute("0") == "🔊 Unmuted"


@pytest.mark.parametrize("arg", ["abc", "-5", "1441", "1.5"])
def test_mute_rejects_bad_minutes(monkeypatch, arg):
    monkeypatch.setattr(mp.state, "muted_until", 0.0)
    assert mp.commands.mute(arg).startswith("Use !mute")
    assert mp.state.mute_remaining() == 0


def test_muted_schedule_is_skipped(monkeypatch):
    sent = []
    fake = type("FakeSender", (), {"channel": lambda self, ch, t: sent.append((ch, t)),
                                   "dm": lambda self, k, t: sent.append((k, t))})()
    monkeypatch.setattr(mp.state, "muted_until", time.monotonic() + 600)
    monkeypatch.setattr(mp.schedule, "due_slot", lambda entry, now: "slot-1")

    async def run_once():
        task = asyncio.create_task(mp.schedule.scheduler(fake, [{"name": "x", "time": "07:30", "channel": 1, "text": "hi"}]))
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(run_once())
    assert sent == []


def test_say(monkeypatch):
    sent = []
    fake = type("FakeSender", (), {"channel": lambda self, ch, t: sent.append((ch, t))})()
    monkeypatch.setattr(mp.state, "tx", fake)
    assert mp.commands.say("1 Net starts 20:00") == "📢 Queued for ch1"
    assert sent == [(1, "Net starts 20:00")]
    assert mp.commands.say("1").startswith("Use !say")
    assert mp.commands.say("one hello").startswith("Use !say")
    assert len(sent) == 1


def test_say_works_while_muted(monkeypatch):
    sent = []
    fake = type("FakeSender", (), {"channel": lambda self, ch, t: sent.append((ch, t))})()
    monkeypatch.setattr(mp.state, "tx", fake)
    monkeypatch.setattr(mp.state, "muted_until", time.monotonic() + 600)
    assert asyncio.run(mp.commands.run_command("!say", "3 Hello", "", {})) == "📢 Queued for ch3"
    assert sent == [(3, "Hello")]


def test_uptime_reply(monkeypatch):
    stats = mp.stats.Stats()
    stats.started -= 3700
    monkeypatch.setattr(mp.stats, "counters", stats)
    monkeypatch.setattr(mp.stats, "_host_uptime", lambda: None)
    assert asyncio.run(mp.commands.run_command("!uptime", "", "Alice", {})).startswith("@[Alice] ⏱️ Bot up 1h 1m")


# ---------- config.toml ----------
@pytest.fixture
def restore_settings():
    saved = {name: getattr(mp.config, name)
             for name in [*mp.config._CONFIG_SETTINGS, "MET_OFFICE_API_KEY", "MET_OFFICE_KEY_SOURCE"]}
    yield
    for name, value in saved.items():
        setattr(mp.config, name, value)


@pytest.mark.skipif(mp.config.tomllib is None, reason="needs Python 3.11+")
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
    assert mp.config.load_config(str(path)) == str(path)
    assert mp.config.CHANNEL_IDXS == [2]
    assert str(mp.config.TIMEZONE) == "UTC"
    assert mp.wx._wx_cache.ttl == 600
    assert mp.config.MIN_TX_GAP_SEC == 5.0
    assert mp.config.RATE_LIMIT_PER_USER == (5, 30.0)
    assert mp.config.ADMIN_PUBKEYS == {"A1B2C3D4E5F6"}
    assert mp.config.LOCATIONS == {"Home": (51.5, -0.1)}
    assert mp.config.SCHEDULED_MESSAGES[0]["name"] == "x"


@pytest.mark.skipif(mp.config.tomllib is None, reason="needs Python 3.11+")
@pytest.mark.parametrize("line", ['use_emoji = "yes"', 'timezone = "Mars/Base"', 'log_level = "LOUD"'])
def test_load_config_rejects_bad_values(tmp_path, restore_settings, line):
    path = tmp_path / "config.toml"
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        mp.config.load_config(str(path))


@pytest.mark.skipif(mp.config.tomllib is None, reason="needs Python 3.11+")
def test_api_key_from_config(tmp_path, restore_settings, monkeypatch):
    monkeypatch.delenv("METOFFICE_API_KEY", raising=False)
    path = tmp_path / "config.toml"
    path.write_text('metoffice_api_key = "  from-config  "\n', encoding="utf-8")
    mp.config.load_config(str(path))
    assert mp.config.MET_OFFICE_API_KEY == "from-config"
    assert mp.config.MET_OFFICE_KEY_SOURCE == str(path)


@pytest.mark.skipif(mp.config.tomllib is None, reason="needs Python 3.11+")
def test_api_key_env_wins_over_config(tmp_path, restore_settings, monkeypatch):
    monkeypatch.setenv("METOFFICE_API_KEY", "from-env")
    path = tmp_path / "config.toml"
    path.write_text('metoffice_api_key = "from-config"\n', encoding="utf-8")
    mp.config.load_config(str(path))
    assert mp.config.MET_OFFICE_API_KEY == "from-env"
    assert mp.config.MET_OFFICE_KEY_SOURCE == "METOFFICE_API_KEY"


def test_load_config_missing_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MESHPOTATO_CONFIG", raising=False)
    monkeypatch.setattr(mp.config, "CONFIG_FILE", str(tmp_path / "config.toml"))
    assert mp.config.load_config() is None
    with pytest.raises(SystemExit):
        mp.config.load_config(str(tmp_path / "missing.toml"))


# ---------- command registry ----------
def test_every_command_and_alias_parses():
    for name, c in mp.commands.COMMANDS.items():
        for word in (name.lstrip("!"),) + c.aliases:
            typed = word if c.plain else "!" + word
            assert mp.commands.parse_command(typed) == (name, ""), typed


def test_every_command_is_documented():
    with open(os.path.join(mp.config.SCRIPT_DIR, "docs", "commands.md"), encoding="utf-8") as fh:
        doc = fh.read()
    for name, c in mp.commands.COMMANDS.items():
        for word in (name,) + tuple("!" + a for a in c.aliases):
            assert re.search(re.escape(word) + r"\b", doc), word


def test_help_lists_each_command_in_its_topic(monkeypatch):
    monkeypatch.setattr(mp.config, "MET_OFFICE_API_KEY", "key")
    for name, c in mp.commands.COMMANDS.items():
        if c.help:
            text = mp.commands.help_text(c.help)
            words = (name,) + tuple("!" + a for a in c.aliases)
            assert any(re.search(re.escape(w) + r"\b", text) for w in words), (c.help, name)
        assert not (c.admin and c.help), f"{name} is admin only, so it stays out of !help"


def test_unknown_command_is_not_allowed():
    assert not mp.commands.command_allowed("!nope", admin=True)
    assert asyncio.run(mp.commands.run_command("!nope", "", "Alice", {})) is None


def test_expand_tokens_fetches_each_token_once(monkeypatch):
    calls = []

    async def fake(place, budget=None, quiet=False):
        calls.append(place)
        return f"<{place or 'home'}>"
    monkeypatch.setitem(mp.reports.REPORTS, "sun", mp.reports.Report(fake))
    monkeypatch.setitem(mp.reports.REPORTS, "moon", mp.reports.Report(fake, place=False))
    out = asyncio.run(mp.reports.expand_tokens("{sun} {sun} {sun:Cromer} {moon} {moon:x} {nope}"))
    assert out == "<home> <home> <Cromer> <home> {moon:x} {nope}"
    assert calls == ["", "Cromer", ""]


# ---------- Bot: radio events to replies ----------
from types import SimpleNamespace

BOT_CONTACTS = {
    "b0b0": {"public_key": "b0b0b0b0b0b0" + "0" * 52, "adv_name": "Bob", "type": 1},
    "ad00": {"public_key": "ad00ad00ad00" + "0" * 52, "adv_name": "Admin", "type": 1},
    "rp00": {"public_key": "e7e7e7e7e7e7" + "0" * 52, "adv_name": "Hill Rpt", "type": 2},
}


@pytest.fixture
def radio_bot(monkeypatch):
    """A Bot on a fake radio, with fresh heard list, mailbox, stats and no mute."""
    monkeypatch.setattr(mp.config, "ADMIN_PUBKEYS", {"ad00ad00ad00"})
    monkeypatch.setattr(mp.config, "USE_EMOJI", False)
    monkeypatch.setattr(mp.heard, "log", mp.heard.Heard())
    monkeypatch.setattr(mp.mail, "box", mp.mail.Mailbox())
    monkeypatch.setattr(mp.stats, "counters", mp.stats.Stats())
    monkeypatch.setattr(mp.state, "muted_until", 0.0)
    radio = SimpleNamespace(self_info={"name": "Potato"}, contacts=BOT_CONTACTS)
    monkeypatch.setattr(mp.state, "radio", radio)
    return mp.bot.Bot(radio)


def sent(b):
    """Everything queued for the radio so far."""
    out = []
    while not b.sender.queue.empty():
        out.append(b.sender.queue.get_nowait())
    return out


def chan_event(text, idx=1):
    return SimpleNamespace(payload={"channel_idx": idx, "text": text})


def dm_event(prefix, text):
    return SimpleNamespace(payload={"pubkey_prefix": prefix, "text": text})


def run_events(b, *events):
    async def go():
        for handler, event in events:
            await handler(event)
        await b.drain()
    asyncio.run(go())


def test_bot_answers_channel_commands(radio_bot):
    run_events(radio_bot, (radio_bot.on_channel_message, chan_event("Alice: ping")))
    [(kind, idx, text)] = sent(radio_bot)
    assert (kind, idx) == ("chan", 1) and text.startswith("@[Alice] Pong")
    assert mp.stats.counters.commands["ping"] == 1 and mp.stats.counters.heard == 1


def test_bot_ignores_itself_other_channels_and_chatter(radio_bot):
    run_events(radio_bot,
               (radio_bot.on_channel_message, chan_event("Potato: ping")),
               (radio_bot.on_channel_message, chan_event("Alice: ping", idx=2)),
               (radio_bot.on_channel_message, chan_event("Alice: ping me later")))
    assert sent(radio_bot) == []


def test_bot_admin_commands_need_an_admin_dm(radio_bot):
    run_events(radio_bot,
               (radio_bot.on_channel_message, chan_event("Alice: !uptime")),
               (radio_bot.on_contact_message, dm_event("b0b0b0b0b0b0", "!uptime")))
    assert sent(radio_bot) == []
    run_events(radio_bot, (radio_bot.on_contact_message, dm_event("ad00ad00ad00", "!uptime")))
    [(kind, key, text)] = sent(radio_bot)
    assert (kind, key) == ("dm", "ad00ad00ad00") and text.startswith("Bot up")


def test_bot_mute_silences_all_but_admins(radio_bot):
    mp.commands.mute("5")
    run_events(radio_bot, (radio_bot.on_channel_message, chan_event("Alice: ping")))
    assert sent(radio_bot) == []
    run_events(radio_bot, (radio_bot.on_contact_message, dm_event("ad00ad00ad00", "ping")))
    assert len(sent(radio_bot)) == 1


def test_bot_rate_limit_tells_the_user_once(radio_bot, monkeypatch):
    monkeypatch.setattr(mp.config, "RATE_LIMIT_PER_USER", (1, 60))
    radio_bot.limiter = mp.ratelimit.RateLimiter()
    run_events(radio_bot, *[(radio_bot.on_channel_message, chan_event("Alice: ping"))] * 3)
    texts = sorted(t for _, _, t in sent(radio_bot))     # the notice goes before the command's reply
    assert len(texts) == 2 and texts[0].startswith("@[Alice] Pong")
    assert texts[1].startswith("@[Alice] Slow down, try again in")
    assert mp.stats.counters.limited == 2


def test_bot_passes_on_mail_when_it_hears_the_recipient(radio_bot):
    mp.mail.box.add("Alice", "a1a1a1a1a1a1", "Bob", "b0b0b0b0b0b0", "see you at 8")
    run_events(radio_bot, (radio_bot.on_contact_message, dm_event("b0b0b0b0b0b0", "hello")))
    [(kind, key, text)] = sent(radio_bot)
    assert (kind, key) == ("dm", "b0b0b0b0b0b0") and "see you at 8" in text
    assert mp.mail.box.messages == []
    assert [e["name"] for e in mp.heard.log.recent(1)] == ["Bob"]


def test_bot_adverts_note_repeaters_but_deliver_no_mail(radio_bot):
    run_events(radio_bot, (radio_bot.on_advert, SimpleNamespace(payload={"public_key": "e7e7e7e7e7e7"})))
    assert [e["name"] for e in mp.heard.log.recent(1, repeaters=True)] == ["Hill Rpt"]
    assert mp.heard.log.recent(1) == []


class FakeRadio:
    """Enough of MeshCore for Bot.run to start, listen and stop."""

    def __init__(self):
        self.self_info, self.contacts = {"name": "Potato"}, dict(BOT_CONTACTS)
        self.subscribed, self.calls = [], []

    def subscribe(self, event_type, handler):
        self.subscribed.append(event_type)
        return event_type

    def unsubscribe(self, sub):
        self.subscribed.remove(sub)

    async def ensure_contacts(self, follow=False):
        self.calls.append("contacts")

    async def start_auto_message_fetching(self):
        self.calls.append("start")

    async def stop_auto_message_fetching(self):
        self.calls.append("stop")

    async def disconnect(self):
        self.calls.append("disconnect")


def test_bot_run_saves_and_disconnects_on_stop(radio_bot, monkeypatch, tmp_path):
    for name in ("HEARD_FILE", "MAIL_FILE", "MAIL_USERS_FILE"):
        monkeypatch.setattr(mp.config, name, str(tmp_path / f"{name.lower()}.json"))
    monkeypatch.setattr(mp.config, "SCHEDULED_MESSAGES", [])
    monkeypatch.setattr(mp.state, "tx", None)
    radio = FakeRadio()
    b = mp.bot.Bot(radio)

    async def go():
        task = asyncio.create_task(b.run())
        await asyncio.sleep(0.05)
        assert mp.bot.EventType.CHANNEL_MSG_RECV in radio.subscribed and mp.state.tx is b.sender
        await b.on_contact_message(dm_event("b0b0b0b0b0b0", "hi"))
        mp.mail.box.add("Bob", "b0b0b0b0b0b0", "Admin", "ad00ad00ad00", "later")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    assert radio.calls == ["start", "contacts", "stop", "disconnect"]
    assert radio.subscribed == []
    assert "Bob" in (tmp_path / "heard_file.json").read_text()
    assert "later" in (tmp_path / "mail_file.json").read_text()   # written only when changed


# ---------- key helpers and caches ----------
def test_key_helpers(monkeypatch):
    assert mp.contacts.key_id(" B0B0B0B0B0B0FFFF ") == "b0b0b0b0b0b0" and mp.contacts.key_id(None) == ""
    assert mp.contacts.contacts_by_key(BOT_CONTACTS, "") == []                   # an empty prefix matches no one
    assert mp.contacts.contact_by_key(BOT_CONTACTS, "B0B0")["adv_name"] == "Bob"
    assert mp.contacts.contact_by_key({**BOT_CONTACTS, "x": {"public_key": "b0b0ff"}}, "b0b0") is None   # two match
    monkeypatch.setattr(mp.config, "ADMIN_PUBKEYS", {" AD00AD00AD00 ", ""})
    assert mp.contacts.is_admin("ad00ad00ad00") and mp.contacts.is_admin("AD00AD00AD00" + "9" * 52)
    assert not mp.contacts.is_admin("") and not mp.contacts.is_admin("b0b0b0b0b0b0")


def test_ttl_cache_follows_its_setting(monkeypatch):
    monkeypatch.setattr(mp.config, "WX_CACHE_SEC", 1234)
    assert mp.wx._wx_cache.ttl == 1234
    monkeypatch.setattr(mp.config, "AURORA_CACHE_SEC", 10)                    # never under AuroraWatch's minimum
    assert mp.aurora._aurora_cache.ttl == mp.aurora.AURORA_MIN_CACHE_SEC
    assert {mp.wx._wx_cache, mp.geo.geocode_cache, mp.alerts._warn_cache, mp.aurora._aurora_cache, mp.air._air_cache,
            mp.bands._hamqsl_cache, mp.bands._tropo_cache} <= set(mp.net.TTLCache.all)
