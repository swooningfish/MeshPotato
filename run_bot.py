#!/usr/bin/env python3
"""
MeshPotato bot (serial companion radio) for use on MeshCore

Built on the patterns in meshcore_py/examples/serial_pingbot.py and meshcore_py/examples/serial_rss_bot.py.

The code is in the meshpotato package next to this file, one module per feature.

Commands (channel or direct message):
  ping               -> Pong with hop count         (whole message, a leading ! is optional)
  test               -> RX in DEFAULT_LOCATION, hop count and your distance (same rules as ping)
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
  !save              -> Write the heard list and the !mail box to disk now (admin DMs only)
  !addmailuser <key> -> Let a public key use !mail (admin DMs only)
  !removemailuser <key> -> Stop a key using !mail (admin DMs only)
  !listmailuser      -> List the keys allowed to use !mail (admin DMs only)
  !help              -> Help topics: !helptest, !helpwx, !helpradio, !helpfun, !helpconv (the ! is required)
  !roll [NdS+M]      -> Roll dice: !roll, !roll d20, !roll 2d6+3
  !flipacoin         -> Heads or tails
  !eightball <q>     -> Ask the eight ball a yes/no question
  !conv <n> <unit> [unit] -> Unit conversion: !conv 10 mi km, !conv 20 c, !conv 868 mhz
  !ohm <two of V/A/Ω/W>   -> Ohm's law and power: !ohm 12v 2a, !ohm 5v 220r, !ohm 10w 50ohm
  !res <colours|value>    -> Resistor colour code both ways: !res yellow violet red gold, !res 4k7
  !who [hours|rpt]        -> People (or repeaters) heard by advert or DM lately, most recent first
  !status <name>          -> When the bot last heard someone, how, and where they are
  !bearing <name|place>   -> Distance and compass direction from you (or the bot) to a contact or place
  !freq [topic]           -> Frequency lists: pmr, cb, ham, hf, marine, air, and mesh (the bot's radio)
  !mail <name> <message>  -> (DM only) Hold a message for someone, sent by DM when the bot next hears them
  !clearmail [name]       -> (DM only) Cancel your own waiting messages, to everyone or to one person

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
import logging

from meshpotato import config as cfg
from meshpotato.bot import main
from meshpotato.config import load_config
from meshpotato.reports import REPORTS
from meshpotato.text import trim
from meshpotato.wx import wx_available

_LOGGER = logging.getLogger("meshpotato_bot")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MeshCore MeshPotato bot")
    ap.add_argument("--port", help=f"serial port, e.g. COM4 or /dev/ttyUSB0 (default {cfg.SERIAL_PORT})")
    ap.add_argument("--config", metavar="FILE", help="settings file (default config.toml next to this script)")
    for name, report in REPORTS.items():
        if report.place:
            ap.add_argument(f"--{name}", metavar="LOCATION", nargs="?", const="",
                            help=f"print the !{name} reply and exit (no radio)")
        else:
            ap.add_argument(f"--{name}", action="store_true", help=f"print the !{name} reply and exit (no radio)")
    args = ap.parse_args()

    loaded = load_config(args.config)
    logging.getLogger().setLevel(cfg.LOG_LEVEL)
    if loaded:
        _LOGGER.info("Settings loaded from %s", loaded)
    wanted = [name for name in REPORTS if getattr(args, name) not in (None, False)]
    try:
        if wanted:
            report = REPORTS[wanted[0]]
            if report.needs_wx and not wx_available():
                raise SystemExit("No Met Office API key set, see steps 4 and 5 in README.md, or docs/settings.md")
            place = getattr(args, wanted[0]) if report.place else ""
            print(trim(asyncio.run(report.fetch(place)) or ""))
        else:
            asyncio.run(main(args.port or cfg.SERIAL_PORT))
    except KeyboardInterrupt:
        print("Stopped")
