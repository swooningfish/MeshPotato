# Command guide

Every MeshPotato command, with example replies and what the symbols mean. For how each feature works behind the scenes, see [How it works](howitworks.md).

- `ping` and `test` must be the whole message. The `!` is optional.
- Every other command starts with `!`.
- Send `!help` for the help topics, then `!helptest`, `!helpnet`, `!helpwx`, `!helpradio`, `!helpfun` or `!helpconv`. `!help wx` works too.
- With `use_emoji = false` in `config.toml`, replies use plain words instead of emojis.

## Contents

- [Places](#places)
- [Ping and test](#ping-and-test)
- [Path and distance](#path-and-distance-path-dist)
- [Who's about](#whos-about-who-status)
- [Mailbox](#mailbox-mail)
- [Bearing](#bearing-bearing)
- [Frequencies](#frequencies-freq)
- [Weather](#weather-wx-wxh-wxf)
- [Weather warnings](#weather-warnings-warn)
- [Sun and moon](#sun-and-moon-sun-moon)
- [Aurora](#aurora-aurora)
- [Air quality and pollen](#air-quality-and-pollen-aq-pollen)
- [Radio conditions](#radio-conditions-hf-vhf-uhf)
- [Unit conversion](#unit-conversion-conv)
- [Ohm's law](#ohms-law-ohm)
- [Resistor colour code](#resistor-colour-code-res)
- [Fun](#fun-roll-flipacoin-eightball)
- [Admin commands](#admin-commands)

---

## Places

Commands marked `[place]` use the bot's `default_location` unless you give one:

| Form | Example |
|------|---------|
| A saved place name | `!wx cambridge` |
| A UK postcode | `!wx NR1 3JU` |
| Half a postcode | `!wx NR1` |
| A UK town or village | `!wx Cromer` |
| Latitude,longitude | `!wx 52.63,1.30` |

Town names cover Great Britain only. Use a postcode for Northern Ireland. If a place can't be found, the bot doesn't reply.

## Ping and test

| Command | Reply |
|---------|-------|
| `ping` | `@[You] 🏓 Pong (2 hops)` |
| `test` | `@[You] 📡 RX in Norwich \| 🐸 (2 hops) \| 📏 34km \| try !path or !route for more info` |

| Emoji | Meaning |
|-------|---------|
| 📡 | Where the bot heard you (`default_location`) |
| 🐸 | Hops: how many repeaters your message went through |
| 📏 | Straight-line distance from you to the bot, if both positions are known |

- A message that came by a direct route shows `(direct route)`.
- With no `default_location` set, the reply starts `Test OK`.
- Distances are in km, or miles with `dist_miles = true`.
- The `try !path or !route for more info` hint is only added when it fits. In a DM it says `try !path for more info`, because `!route` only works on a channel.

## Path, route and distance (!path, !route, !dist)

| Command | Reply |
|---------|-------|
| `!path` | `@[You] 🛤️ 3 hops: a1 Norwich Cath › b2 › c3` |
| `!route` | `@[You] 🗺️ https://meshrank.net/path/65160` |
| `!dist` | `@[You] 📏 You ›15km› a1 ›?› b2 ›5.0km› Bot \| 20km+, 1 of 3 legs unknown \| 34km direct` |

Alias: `!trace` = `!path`.

**`!path`** lists the repeaters your message came through, first one first. Each repeater shows as the short code MeshCore uses for it, plus its name when the bot can be sure which repeater it is.

- `0 hops, heard directly` means no repeater was involved.
- If the path is too long for one message, the names are dropped, then the last repeaters become `+2 more`.

**`!route`** replies with a link to your `!route` message on [MeshRank](https://meshrank.net), which shows the route it took on a map. The link is valid for 24 hours.

- It only works on a public or hashtag channel (such as `#test`), because MeshRank can't read DMs or private channels.
- MeshRank needs its observers to hear your message. The bot waits up to `meshrank_wait_sec` (10 s), then replies `MeshRank hasn't heard your message yet`.
- Turn it off with `meshrank_links = false`. The bot then ignores `!route`.

**`!dist`** shows the same path with the distance of each leg:

| Part | Meaning |
|------|---------|
| `›15km›` | Straight-line distance between two points |
| `›?›` | One end of that leg has no known position |
| `20km+` | Total of the known legs. `+` means some legs are unknown |
| `34km direct` | Straight line from you to the bot. Compare with the total to see how far the path wandered |

## Who's about (!who, !status)

| Command | Reply |
|---------|-------|
| `!who` | `@[You] 👥 4 heard in 24h: Alice 2m, Bob 15m, Carol 3h, Dave 20h` |
| `!who 2` | Only people heard in the last 2 hours |
| `!who rpt` | `@[You] 👥 2 repeaters heard in 24h: Aylsham RPT 10m, Hill Top 4h` |
| `!status bob` | `@[You] 👤 Bob: heard 3h ago by advert \| 📍 34km N of bot` |
| `!status alice` | `@[You] 👤 Alice: heard 2m ago by DM, 2 hops, SNR 7.5dB` |
| `!status b0b0` | Look someone up by the start of their public key |
| `!status dave` | `@[You] 👤 Dave: last advert 2h ago` |

Aliases: `!heard` = `!who`. `!seen` and `!lastheard` = `!status`.

- `!who rpt` is a quick way to see which parts of the mesh are up.
- Someone who only chats on a channel shows up once the bot hears their advert.
- If several people match a `!status` name, the bot lists them.
- Works without internet.

## Mailbox (!mail)

`!mail` only works in a **DM to the bot**, and only for approved users (an admin adds you with `!addmailuser`).

| DM to the bot | Reply |
|---------------|-------|
| `!mail bob see you at the hall at 8` | `📮 Held for Bob (1/10), sent by DM when the bot next hears them` |
| `!mail @[Sam 🐬 Base] on my way` | `📮 Held for Sam 🐬 Base (1/10), sent by DM when the bot next hears them` |
| `!mail` | `📮 Use !mail <name> <message>. Waiting: Bob 2, Sam 🐬 Base 1. !clearmail to cancel` |
| `!clearmail bob` | `📮 Cleared 2 messages to Bob` |
| `!clearmail` | `📮 Cleared 3 waiting messages` |

The recipient gets a DM from the bot:

```
📬 Alice 2h ago: see you at the hall at 8
```

Aliases: `!msg` and `!leave` = `!mail`. `!mailclear` and `!unmail` = `!clearmail`.

- **Names:** the whole contact name (emoji optional, so `sam base` finds `Sam 🐬 Base`), an `@[mention]`, or the first word if only one contact starts with it.
- **Who can receive:** any companion in the bot's contacts. Repeaters can't.
- **Limits:** 100 bytes per message, 10 waiting per recipient, 30 waiting in total per sender. Messages not delivered in 7 days are dropped.

## Bearing (!bearing)

| Command | Reply |
|---------|-------|
| `!bearing alice` | `@[You] 🧭 Alice: 2.3km NE (48°) from you` |
| `!bearing aylsham` | `@[You] 🧭 Aylsham RPT: 15km S (192°) from you` |
| `!bearing NR1` | `@[You] 🧭 NR1: 34km S (181°) from you` |
| `!bearing 52.93,1.50` | `@[You] 🧭 52.93,1.50: 13km E (90°) from you` |

Aliases: `!brg` and `!find`.

- Measured from your position if the bot knows it, otherwise from the bot. The reply says which.
- Give a contact name or any [place](#places). Contacts, saved places and `lat,lon` work without internet.
- The bearing is from true north. In the UK that's within a degree or two of a compass reading.

## Frequencies (!freq)

| Command | Reply |
|---------|-------|
| `!freq` | `📻 Frequencies: !freq pmr, cb, ham, hf, marine, air, mesh` |
| `!freq pmr` | `📻 PMR446 MHz: 1 446.00625, 2 .01875, 3 .03125, …` |
| `!freq mesh` | `📻 MeshCore here: 869.618 MHz, BW 62.5kHz, SF8, CR8` |

| Topic | What it lists |
|-------|---------------|
| `pmr` | PMR446 licence-free handheld channels 1 to 8 |
| `cb` | UK 27/81 and EU CEPT CB, with emergency channel 9 |
| `ham` | Amateur FM calling frequencies on 2m, 70cm, 6m and 4m |
| `hf` | IARU Region 1 HF emergency centres of activity |
| `marine` | Marine VHF channels 16, 67 and 70 |
| `air` | 121.500 MHz aviation distress |
| `mesh` | The bot radio's own frequency and LoRa settings |

Add your own topics, such as local repeaters, with `[freq_lists]` in `config.toml` (see the [settings guide](settings.md)).

Check these lists against current band plans before relying on them. Amateur and marine VHF need a licence to transmit, except in a real emergency.

Aliases: `!freqs`, `!frequency`.

## Weather (!wx, !wxh, !wxf)

Needs a Met Office API key. Without one, these commands are ignored.

| Command | Reply |
|---------|-------|
| `!wx [place]` | Current weather |
| `!wxh [place]` | Hour-by-hour outlook |
| `!wxf [place]` | 3-day forecast |

```
🌙 Norwich 23:00 Clear 🌡️14°C (feels 13°) 💨NW 6mph gust 15 ☔0% 💧humidity 73%
🕒 Norwich: 23h ☁️13° ☔10% | 00h ☁️13° ☔20% | 01h 🌧️12° ☔30% | 02h 🌧️11° ☔40%
📅 Norwich: Wed ☁️ 17/8° ☔10% | Thu 🌧️ 18/8° ☔60% | Fri ☀️ 19/8° ☔5%
```

| Symbol | Meaning |
|--------|---------|
| 🌡️ 14°C | Temperature |
| (feels 13°) | Feels-like temperature |
| 💨 NW 6mph | Wind direction (where it blows from) and speed |
| gust 15 | Peak gust speed |
| ☔ 0% | Chance of rain |
| 💧 humidity 73% | Relative humidity |
| 17/8° | Day high / night low |
| 23h | Hour of the day (23h = 23:00) |

About 4 hours fit in one `!wxh` message. To cover a longer period, set `wxh_step_hours = 3` to show every third hour.

## Weather warnings (!warn)

| Command | Reply |
|---------|-------|
| `!warn [place]` | Met Office warnings for that place's region |
| `!warn <region code>` | Warnings for a region, such as `!warn nw` or `!warn uk` |

Alias: `!warnings`. No API key needed.

```
⚠️ East of England: 🟠💨 Wind Thu 18:00-Fri 12:00 | 🟡🌧️ Rain Sat 06:00-21:00
✅ No weather warnings for East of England
```

| Symbol | Meaning |
|--------|---------|
| 🔴 🟠 🟡 | Red, amber or yellow warning |
| 🌧️ 💨 ❄️ 🧊 ⛈️ ⚡ 🌫️ 🌡️ | Rain, wind, snow, ice, thunderstorms, lightning, fog, extreme heat |
| `Thu 18:00-Fri 12:00` | When the warning is valid |
| `to Fri 12:00` | The warning has already started |
| `+2 more` | More warnings than fit in one message |

**Alerts:** for 24 hours after a `!warn`, the bot posts to the same channel or DM whenever the warnings change:

```
🔔 ⚠️ East of England: 🔴💨 Wind Thu 18:00-Fri 12:00 | 🟡🌧️ Rain Sat 06:00-21:00
```

**Region codes:**

| Code | Region | Code | Region |
|------|--------|------|--------|
| `uk` | Whole UK | `ni` | Northern Ireland |
| `os` | Orkney & Shetland | `wl` | Wales |
| `he` | Highlands & Eilean Siar | `nw` | North West England |
| `gr` | Grampian | `ne` | North East England |
| `st` | Strathclyde | `yh` | Yorkshire & Humber |
| `ta` | Central, Tayside & Fife | `wm` | West Midlands |
| `dg` | SW Scotland, Lothian & Borders | `em` | East Midlands |
| `ee` | East of England | `sw` | South West England |
| `se` | London & South East England | | |

## Sun and moon (!sun, !moon)

| Command | Reply |
|---------|-------|
| `!sun [place]` | Sunrise, sunset and hours of daylight today |
| `!moon` | Moon phase, how much is lit, and the next full and new moon |

Aliases: `!sunrise`, `!sunset`. Works without internet (except to look up a place name).

```
Norwich Thu 24 Sep 🌅 06:43 🌇 18:50 ☀️ 12h06m daylight
🌔 Waxing gibbous, 95% lit | 🌕 Full Sat 26 Sep | 🌑 New Sat 10 Oct
```

| Emoji | Phase | Emoji | Phase |
|-------|-------|-------|-------|
| 🌑 | New moon | 🌕 | Full moon |
| 🌒 | Waxing crescent | 🌖 | Waning gibbous |
| 🌓 | First quarter | 🌗 | Last quarter |
| 🌔 | Waxing gibbous | 🌘 | Waning crescent |

## Aurora (!aurora)

Alias: `!solar`. Data from [AuroraWatch UK](https://aurorawatch.lancs.ac.uk/).

```
🟢 Green: No significant activity | 11nT now, 62nT peak 24h | AuroraWatch UK
🟠 Amber: Amber alert: possible aurora | 131nT now, 213nT peak 24h | AuroraWatch UK
```

| Part | Meaning |
|------|---------|
| 🟢 🟡 🟠 🔴 | Alert level: green, yellow (from 50 nT), amber (from 100 nT), red (from 200 nT) |
| `11nT now` | Magnetic disturbance so far this hour |
| `62nT peak 24h` | Highest disturbance in the last 24 hours |

Aurora activity upsets HF radio but barely affects MeshCore, which runs on UHF.

## Air quality and pollen (!aq, !pollen)

| Command | Reply |
|---------|-------|
| `!aq [place]` | Air quality index and main pollutants now |
| `!pollen [place]` | Pollen forecast for the next 24 hours |

Alias: `!air` = `!aq`.

```
🟢 Norwich air: Fair (EAQI 22) | PM2.5 5 PM10 9 NO2 7 O3 63 µg/m³ | Open-Meteo
🌼 Norwich pollen 24h: Grass 45 Moderate | Alder 3 grains/m³ | Open-Meteo
```

| EAQI | Band | | Pollutant | What it is |
|------|------|-|-----------|------------|
| 0-20 | 🟢 Good | | PM2.5 | Fine particles |
| 20-40 | 🟢 Fair | | PM10 | Coarse particles |
| 40-60 | 🟡 Moderate | | NO2 | Nitrogen dioxide, mostly traffic |
| 60-80 | 🟠 Poor | | O3 | Ozone |
| 80-100 | 🔴 Very poor | | | |
| over 100 | 🟣 Extremely poor | | | |

- This is the European index, not the UK's 1 to 10 scale, so the numbers won't match UK-AIR.
- Pollen covers grass, birch, alder, mugwort and ragweed, highest first. Grass also gets a level: Low, Moderate (from 30), High (from 50), Very high (from 150).
- Outside the pollen season the reply is `None forecast`.

## Radio conditions (!hf, !vhf, !uhf)

| Command | Reply |
|---------|-------|
| `!hf` | HF band conditions now (day or night), solar flux, K and A index, noise |
| `!vhf` | 6m, 4m and 2m E-skip, VHF aurora and tropo |
| `!uhf [place]` | UHF tropo now and the best in the next 24 hours |

Aliases: `!bands` = `!hf`, `!tropo` = `!uhf`.

```
📻 HF night: 80-40m🟢 30-20m🟢 17-15m🟡 12-10m🔴 | ☀️SFI 112 🧲K2 A19 | 🔊S1-S2 | N0NBH
📡 VHF: 6m Es🟢 50MHz ES 4m Es🔴 2m Es🔴 Aurora🔴 | Tropo ➖Normal | N0NBH, Open-Meteo
📶 Norwich UHF tropo: 🔼Slightly enhanced now (-65 N/km) | 24h best ⏫Enhanced Thu 06h (-89) | Open-Meteo
```

| Emoji | Meaning |
|-------|---------|
| 🟢 🟡 🔴 | HF: good, fair, poor. VHF: 🟢 open, 🔴 closed |
| ☀️ SFI | Solar flux index. Higher is better for the upper HF bands |
| 🧲 K / A | Geomagnetic indexes. Lower is quieter and better |
| 🔊 | Expected noise level (S-units) |
| 🔽 ➖ 🔼 ⏫ 🚀 | Tropo: below normal, normal, slightly enhanced, enhanced, ducting likely |

`!uhf` is the useful one for MeshCore's own 868 MHz: enhanced tropo means longer range than usual.

## Unit conversion (!conv)

| Command | Reply |
|---------|-------|
| `!conv 10 mi km` | `📐 10 mi = 16.09 km` |
| `!conv 10mi to km` | `📐 10 mi = 16.09 km` |
| `!conv 20 c` | `📐 20°C = 68°F` |
| `!conv 70 mph` | `📐 70 mph = 112.7 km/h` |
| `!conv 1013 hpa` | `📐 1013 hPa = 29.91 inHg` |
| `!conv 5 w dbm` | `📐 5 W = 36.99 dBm` |
| `!conv 868 mhz` | `📐 868 MHz = 34.54 cm` (wavelength) |
| `!conv 2 m mhz` | `📐 2 m = 149.9 MHz` |

Aliases: `!convert`, `!units`.

Give one unit to convert to its usual partner, or two to choose. `to` is optional. Unit names aren't case sensitive.

| Kind | Units |
|------|-------|
| Length | `mm`, `cm`, `m`, `km`, `in`, `ft`, `yd`, `mi`, `nmi` |
| Weight | `g`, `kg`, `oz`, `lb`, `st` |
| Volume | `ml`, `l`, `floz`, `pt`, `gal` (UK), `usgal` |
| Speed | `m/s`, `km/h` (`kph`), `mph`, `kn` |
| Pressure | `hPa` (`mb`), `inHg`, `mmHg`, `psi`, `bar` |
| Temperature | `c`, `f`, `k` |
| Power | `mW`, `W`, `kW`, `dBm` |
| Frequency | `Hz`, `kHz`, `MHz`, `GHz` (converts to and from wavelength) |

## Ohm's law (!ohm)

Give any two of voltage, current, resistance and power, and the bot works out the other two.

| Command | Reply |
|---------|-------|
| `!ohm 12v 2a` | `⚡ 12 V, 2 A → 6 Ω, 24 W` |
| `!ohm 5v 220r` | `⚡ 5 V, 220 Ω → 22.73 mA, 113.6 mW` |
| `!ohm 100w 13.8v` | `⚡ 13.8 V, 100 W → 7.246 A, 1.904 Ω` |
| `!ohm 4.7k 20ma` | `⚡ 20 mA, 4.7 kΩ → 94 V, 1.88 W` |

Aliases: `!ohms`, `!vir`, `!ohmslaw`.

- Units: `v`, `a`, `w`, and `ohm`, `r` or `Ω`.
- Prefixes: `u`/`µ` (micro), `m` (milli), `k` (kilo), `M` (mega). Case matters: `1M` is 1 MΩ, `20mA` is 20 mA.
- A number with only a prefix is a resistance: `4.7k` = 4.7 kΩ.

## Resistor colour code (!res)

Give the colours to get the value, or the value to get the colours.

| Command | Reply |
|---------|-------|
| `!res yellow violet red gold` | `🟨🟪🟥🥇 Yellow Violet Red Gold = 4.7 kΩ ±5%` |
| `!res brown black red` | `🟫⬛🟥 Brown Black Red = 1 kΩ ±20%` |
| `!res 4k7` | `4.7 kΩ: 🟨🟪🟥🥇 Yellow Violet Red Gold ±5% \| 🟨🟪⬛🟫🟫 Yellow Violet Black Brown Brown ±1%` |
| `!res 10k 1%` | `10 kΩ: 🟫⬛🟧🟫 Brown Black Orange Brown ±1% \| 🟫⬛⬛🟥🟫 Brown Black Black Red Brown ±1%` |

Aliases: `!resistor`, `!colour`, `!color`.

- 3 to 6 bands. Separate colours with spaces, commas or hyphens. Bands can be given either way round.
- Colours: `black`, `brown`, `red`, `orange`, `yellow`, `green`, `blue`, `violet` (`purple`), `grey` (`gray`), `white`, `gold`, `silver`. Short codes like `bk`, `bn`, `rd` work too.
- Values: `470`, `470r`, `4.7k`, `4k7`, `4r7`, `1M` or `220 ohm`. Add `1%`, `2%` or `10%` to pick the tolerance band.
- Grey, gold and silver show as 🩶, 🥇 and 🥈.

## Fun (!roll, !flipacoin, !eightball)

| Command | Reply |
|---------|-------|
| `!roll` | `🎲 1d6: 4` |
| `!roll d20` | `🎲 1d20: 17` |
| `!roll 2d6` | `🎲 2d6: 3 + 5 = 8` |
| `!roll 3d8+2` | `🎲 3d8+2: 5 + 3 + 2 + 2 = 12` |
| `!flipacoin` | `🪙 Heads` or `🪙 Tails` |
| `!eightball <question>` | `🎱 Signs point to yes.` |

Aliases: `!dice` = `!roll`. `!flip` and `!coin` = `!flipacoin`. `!8ball` = `!eightball`.

`2d6` means two six-sided dice. Up to 10 dice with up to 1000 sides. `!eightball` needs a question.

## Admin commands

Admins are the public keys listed in `admin_pubkeys` in `config.toml` (the first 12 characters is enough). These commands only work in a **DM to the bot**, and aren't shown in `!help`.

| Command | What it does |
|---------|--------------|
| `!mute <minutes>` | Silence the bot for 1 to 1440 minutes: `🔇 Muted for 30m, until 20:30` |
| `!mute` | Show whether the bot is muted |
| `!unmute` (or `!mute 0`) | End the mute early |
| `!say <ch> <text>` | Post to a channel as the bot: `!say 1 Net starts 20:00` |
| `!save` | Save the heard list and mailbox to disk now (do this before a planned power-off) |
| `!addmailuser <key>` | Let someone use `!mail`. Give at least the first 12 characters of their key |
| `!removemailuser <key>` | Stop someone using `!mail` |
| `!listmailuser` | List who can use `!mail` |
| `!stats` | Commands served, messages heard and sent, API calls, radio battery |
| `!uptime` | How long the bot and the Pi have been running |

Aliases: `!addmailusers`, `!removemailusers`, `!listmailusers`.

```
📊 Cmds 42 (wx 20, ping 12, test 5) | Heard 310 | Sent 45 | Limited 3 | WX API 18/300 | 🔋4.02V
⏱️ Bot up 3d 4h 12m (since Mon 21 Sep 09:14) | System up 12d 3h 40m
```

| `!stats` field | Meaning |
|----------------|---------|
| Cmds | Commands answered, with the busiest ones |
| Heard | Messages heard on the bot's channels and in DMs |
| Sent | Messages sent. `Failed` appears if any failed |
| Limited | Commands dropped by the rate limit |
| WX API | Met Office calls today, out of the daily budget |
| 🔋 | Radio battery voltage |

While muted, the bot ignores everyone except admin DMs, and skips scheduled messages.
