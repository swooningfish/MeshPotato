# MeshPotato 🥔: MeshCore WX, Ping, Bot

MeshPotato is a chat bot for a [MeshCore](https://github.com/meshcore-dev) mesh radio network. It runs on a Raspberry Pi with a MeshCore companion radio plugged in over USB. It listens on your chosen channels and in direct messages, and replies to commands such as `ping`, `!wx` or `!help`.

What it can do:

- 🏓 **Signal checks:** `ping`, `test` and `!path` show hops, SNR, RSSI and the repeaters your message went through
- 🌦️ **Weather:** current weather, hourly outlook and 3-day forecast from the Met Office, plus weather warnings with automatic change alerts
- 🌅 **Sky:** sunrise, sunset, moon phase and aurora alerts
- 🌼 **Air:** air quality and pollen forecasts
- 📻 **Radio conditions:** HF band conditions, VHF E-skip and UHF tropo
- 🎲 **Fun:** dice, coin flip and a magic eight ball
- 🛡️ **Admin tools:** mute the bot, post as the bot, view stats. Built-in rate limiting stops spam
- ⏰ **Scheduled messages:** a morning forecast, a beacon, anything you like at set times

It runs as a systemd service, so it starts on boot and restarts if it crashes.

---

## Contents

- [Quick start](#quick-start)
- [Using the bot](#using-the-bot)
- [Setup guide](#setup-guide)
  - [1. What you need](#1-what-you-need)
  - [2. Get a Met Office API key](#2-get-a-met-office-api-key)
  - [3. Install the software](#3-install-the-software)
  - [4. Configure the bot](#4-configure-the-bot)
  - [5. Test it](#5-test-it)
  - [6. Install as a service](#6-install-as-a-service)
  - [7. Manage and update](#7-manage-and-update)
- [Troubleshooting](#troubleshooting)
- [Command reference](#command-reference)
- [Settings reference](#settings-reference)
- [Scheduled messages](#scheduled-messages)
- [Files in this repo](#files-in-this-repo)
- [To do](#to-do) · [Credits](#credits) · [License](#license)

---

## Quick start

On the Pi, as your normal user (`alarm` on Arch Linux ARM), not root:

```bash
# 1. Install packages and the meshcore library
sudo pacman -S --needed python python-pip tzdata git
pip install --user --break-system-packages meshcore

# 2. Let your user open the radio's serial port, then log out and back in
sudo usermod -aG uucp alarm

# 3. Get the bot
cd ~
git clone https://github.com/swooningfish/MeshPotato.git
cd MeshPotato

# 4. Create your settings file and add your Met Office key, channels and location
cp config.example.toml config.toml
nano config.toml

# 5. Try it in the foreground (Ctrl+C to stop)
python run_bot.py --wx norwich
python run_bot.py

# 6. Install it as a service so it runs on boot
chmod +x install_mesh_potato_bot_service.sh
./install_mesh_potato_bot_service.sh
```

Then send `ping` from another node on one of the bot's channels. You should get `🏓 Pong` back.

Each step is explained in the [Setup guide](#setup-guide). The Met Office key is optional: without it, everything except `!wx`, `!wxh` and `!wxf` still works.

---

## Using the bot

Send commands in a channel the bot listens on (channels 1 and 3 by default), or as a direct message to the bot.

**Two rules:**

1. `ping` and `test` must be the **whole message**. The `!` is optional. "ping me later" is ignored.
2. Every other command **starts with `!`**. Ordinary chat that happens to start with "wx" or "help" doesn't trigger anything.

Send `!help` on the mesh for the command list.

### Command cheat sheet

| Command | What you get |
|---------|--------------|
| `ping` | `🏓 Pong (2 hops)` |
| `test` | Where the bot heard you, hops, SNR, RSSI and your distance |
| `!path` | The repeaters your message came through |
| `!dist` | Distance of each leg along that path |
| `!wx [place]` | Current weather |
| `!wxh [place]` | Hour-by-hour outlook |
| `!wxf [place]` | 3-day forecast |
| `!warn [place or region]` | Met Office weather warnings, then alerts if they change |
| `!sun [place]` | Sunrise, sunset and daylight |
| `!moon` | Moon phase and next full and new moon |
| `!aurora` | Aurora alert level from AuroraWatch UK |
| `!aq [place]` | Air quality |
| `!pollen [place]` | Pollen forecast |
| `!hf` | HF band conditions |
| `!vhf` | 6m, 4m, 2m E-skip, VHF aurora and tropo |
| `!uhf [place]` | UHF tropo (useful for MeshCore's own 868 MHz) |
| `!roll [dice]` | Dice roll, such as `!roll 2d6` |
| `!flipacoin` | Heads or tails |
| `!eightball <question>` | Magic eight ball |
| `!help` | Command list |

`[place]` is optional. Leave it out to use the bot's default location. It can be:

| Form | Example |
|------|---------|
| A saved place name | `!wx cambridge` |
| A UK postcode | `!wx NR1 3JU` |
| Half a postcode | `!wx NR1` |
| A UK town or village | `!wx Cromer` |
| Latitude,longitude | `!wx 52.63,1.30` |

Admins also get `!mute`, `!unmute`, `!say`, `!stats` and `!uptime`, in direct messages only. See [Admin commands](#admin-commands).

Full details, example replies and what every emoji means are in the [Command reference](#command-reference).

---

## Setup guide

### 1. What you need

- A Raspberry Pi running Arch Linux ARM (the default user is `alarm`). Other Linux systems work too. On Debian or Raspberry Pi OS, use `apt` for packages and the `dialout` group instead of `uucp`
- A MeshCore companion radio with **USB serial** firmware, plugged into the Pi
- Python 3.11 or newer (3.10 runs, but can't read `config.toml`)
- Internet access for the Pi
- Optional: a free Met Office DataHub API key, for the weather commands

### 2. Get a Met Office API key

Skip this if you don't need `!wx`, `!wxh` and `!wxf`.

1. Register at https://datahub.metoffice.gov.uk/
2. Subscribe to the **Site Specific** (Global Spot) free plan.
3. Copy the API key. It is about 1670 characters long, so copy all of it.

You'll add it to the bot in [step 4](#4-configure-the-bot).

The free plan allows 360 calls a day. To stay inside that, the bot:

- caches each forecast for 30 minutes (`wx_cache_sec`)
- stops calling the Met Office after 300 calls in a UTC day (`wx_daily_call_budget`). Cached forecasts are still served. New lookups reply `WX: daily quota used, try tomorrow` until midnight UTC.

The call count is kept in memory, so it starts again at 0 when the bot restarts.

All the other data sources (warnings, postcodes, aurora, air quality, radio conditions) are free and need no key.

### 3. Install the software

#### 3.1 Packages

```bash
sudo pacman -Syu
sudo pacman -S --needed python python-pip tzdata git
```

#### 3.2 The meshcore Python library

Choose one.

**Option A, system-wide** (simplest on a Pi that only runs the bot):

```bash
pip install --user --break-system-packages meshcore
```

**Option B, a virtual environment** (keeps it separate from the system Python):

```bash
python -m venv ~/meshbot
~/meshbot/bin/pip install meshcore
```

With option B, run the bot with `~/meshbot/bin/python` instead of `python`, and add `--python ~/meshbot/bin/python` when you [install the service](#6-install-as-a-service).

No other packages are needed. The bot uses only modules built into Python apart from `meshcore`.

#### 3.3 Serial port access

On Arch the radio's port belongs to the `uucp` group. Add yourself to it:

```bash
sudo usermod -aG uucp alarm
```

Log out and back in, then check:

```bash
groups                 # should list uucp
ls -l /dev/ttyACM*     # crw-rw---- 1 root uucp ...
ls /dev/serial/by-id/  # a stable name for your radio
```

Don't run the bot with `sudo` to get around a permission error. sudo also drops your environment variables, including the API key.

#### 3.4 Download the bot

As the `alarm` user:

```bash
cd ~
git clone https://github.com/swooningfish/MeshPotato.git
cd MeshPotato
chmod +x install_mesh_potato_bot_service.sh
```

The service runs the bot from `~/MeshPotato`, so leave it there. Run every later command in this guide from that folder.

### 4. Configure the bot

All your settings go in `config.toml`. Don't edit `run_bot.py`: `config.toml` is ignored by git, so `git pull` never clashes with your changes.

```bash
cp config.example.toml config.toml
nano config.toml
```

The file is fully commented. Every line starts with `#`, which means it's switched off and the built-in default is used. To change a setting, remove the `#` at the start of its line and edit the value.

Most people only need to change these:

```toml
metoffice_api_key = "paste-your-key-here"   # whole key on one line
channel_idxs = [1, 3]                       # channel slots the bot listens and replies on
default_location = "Norwich"                # used when no place is given
admin_pubkeys = ["a1b2c3d4e5f6"]            # your public key, first 12 hex characters
```

If you put the API key in `config.toml`, keep the file private:

```bash
chmod 600 config.toml
```

#### Finding your channel numbers

`channel_idxs` uses the slot numbers of the channels on the bot's radio. To see them, stop the bot if it's running and run:

```bash
python channel_list.py
```

It prints the radio's name, its public key and each channel with its slot number:

```
Channels:
    0  Public
    1  Norfolk
    3  Test
```

It uses `serial_port` from `config.toml`, or pass the port: `python channel_list.py /dev/ttyACM0`.

#### Other ways to store the API key

The service doesn't read `~/.bashrc`. If you'd rather not keep the key in `config.toml`, put it in a key file:

```bash
mkdir -p ~/.config/meshcore
nano ~/.config/meshcore/metoffice_key     # paste the key, save
chmod 600 ~/.config/meshcore/metoffice_key
```

The bot uses the first key it finds, in this order:

1. `METOFFICE_API_KEY` environment variable
2. `metoffice_api_key` in `config.toml`
3. The file named in `METOFFICE_KEY_FILE`
4. `~/.config/meshcore/metoffice_key`
5. `metoffice_key.txt` in the same folder as the script

The startup log says which one it used. If there's no key, the weather commands are switched off and the rest of the bot keeps working.

Every setting is listed in the [Settings reference](#settings-reference).

By default the bot posts a morning weather message and a weekend forecast to channel 1. To change or turn off these posts, see [Scheduled messages](#scheduled-messages).

### 5. Test it

#### Without the radio

These print a reply and exit, so you can check the internet side first. Leave out the place to use `default_location`:

```bash
python run_bot.py --wx norwich
python run_bot.py --wxh norwich
python run_bot.py --wxf "NR1 3JU"
python run_bot.py --warn Cromer
python run_bot.py --sun NR1
python run_bot.py --moon
python run_bot.py --aurora
python run_bot.py --aq
python run_bot.py --pollen
python run_bot.py --hf
python run_bot.py --vhf
python run_bot.py --uhf
```

#### With the radio

Run the full bot in the foreground (Ctrl+C to stop):

```bash
python run_bot.py --port /dev/ttyACM0
```

You should see lines like these:

```
Settings loaded from /home/alarm/MeshPotato/config.toml
Met Office API key loaded from /home/alarm/MeshPotato/config.toml (1670 chars)
Connected on /dev/ttyACM0
Scheduler running with 3 entries
Listening on channels [1, 3] and DMs
```

From another node, send `ping`, `test`, `!wx`, `!roll 2d6` and `!help` on one of the bot's channels and check for the replies.

**Stop the foreground bot before installing the service.** Only one program can open the serial port at a time.

#### Run the automated tests (optional)

The tests don't need the radio, the `meshcore` library or an API key:

```bash
sudo pacman -S --needed python-pytest
python -m pytest tests
```

### 6. Install as a service

From `~/MeshPotato`, as the `alarm` user (not root, the script asks for sudo when it needs it):

```bash
./install_mesh_potato_bot_service.sh
```

That's usually all you need. Options:

| Option | Default | Purpose |
|--------|---------|---------|
| `--port` | first `/dev/serial/by-id/*` | Serial port |
| `--python` | `python3` on PATH | Python to use, for example `~/meshbot/bin/python` for a venv |
| `--script` | `run_bot.py` | Which bot file to run |
| `--name` | `meshcore-meshpotato-bot` | Service name |
| `--uninstall` | | Remove the service |

For example:

```bash
./install_mesh_potato_bot_service.sh --port /dev/ttyACM0
./install_mesh_potato_bot_service.sh --python ~/meshbot/bin/python
```

The installer:

1. Checks the script exists and `meshcore` imports.
2. Adds you to the `uucp` group if needed.
3. Sets `config.toml` to mode 600 if it holds the API key. Otherwise, if `METOFFICE_API_KEY` is set in your shell and the key file is missing, it writes the key file.
4. Picks a stable `/dev/serial/by-id/` port name if you don't give `--port`.
5. Writes `/etc/systemd/system/meshcore-meshpotato-bot.service`.
6. Disables ModemManager if it's running, because it grabs `ttyACM` radios.
7. Enables and starts the service.

<details>
<summary>The service file it writes</summary>

```ini
[Unit]
Description=MeshCore MeshPotato bot
After=network-online.target time-sync.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=alarm
Group=alarm
SupplementaryGroups=uucp
WorkingDirectory=/home/alarm/MeshPotato
Environment=HOME=/home/alarm
Environment=PYTHONUNBUFFERED=1
ExecStart="/usr/bin/python3" "/home/alarm/MeshPotato/run_bot.py" --port "/dev/serial/by-id/usb-..."
Restart=always
RestartSec=15
KillSignal=SIGINT
TimeoutStopSec=15
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

</details>

### 7. Manage and update

| Task | Command |
|------|---------|
| Status | `sudo systemctl status meshcore-meshpotato-bot` |
| Live logs | `journalctl -u meshcore-meshpotato-bot -f` |
| Logs since boot | `journalctl -u meshcore-meshpotato-bot -b` |
| Restart (after changing `config.toml`) | `sudo systemctl restart meshcore-meshpotato-bot` |
| Stop | `sudo systemctl stop meshcore-meshpotato-bot` |
| Start | `sudo systemctl start meshcore-meshpotato-bot` |
| Turn off at boot | `sudo systemctl disable meshcore-meshpotato-bot` |
| Remove | `./install_mesh_potato_bot_service.sh --uninstall` |

To update to the latest version:

```bash
cd ~/MeshPotato
git pull
sudo systemctl restart meshcore-meshpotato-bot
```

Your `config.toml` is kept.

Stop the service before running the bot or `channel_list.py` by hand, or they fight over the serial port.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Permission denied` on `/dev/ttyACM0` | Add yourself to `uucp` (section 3.3), then log out and back in |
| `bad interpreter` or `$'\r': command not found` | The files have Windows line endings. Run `sed -i 's/\r$//' install_mesh_potato_bot_service.sh run_bot.py channel_list.py` |
| Port busy, or the bot can't connect | Another program has the port. Stop the service, or the foreground bot. The installer disables ModemManager, which also grabs radios |
| The bot doesn't answer `!wx` | No API key was found. Check the startup log, and see section 4 |
| The bot doesn't answer anything | Check `channel_idxs` matches the channel you're sending on (`python channel_list.py`), and that the bot isn't muted |
| `!wx somewhere` gets no reply | The place wasn't found. Try a postcode. Place names cover Great Britain only, so use a postcode for Northern Ireland |
| The bot stops at startup naming a setting | That setting in `config.toml` has the wrong type, such as `use_emoji = "yes"` instead of `true`. A misspelled setting name is only logged and ignored |
| Scheduled messages at the wrong time | Check the Pi's clock with `timedatectl`. If NTP is off, run `sudo timedatectl set-ntp true` |
| `Slow down, try again in 42s` | You hit the per-user rate limit. See [Rate limiting](#rate-limiting) |

To see every message the bot hears, set `log_level = "DEBUG"` in `config.toml`, restart and watch `journalctl -u meshcore-meshpotato-bot -f`.

---

## Command reference

### Ping and test

| Command | Reply |
|---------|-------|
| `ping` | `@[You] 🏓 Pong (2 hops)` |
| `test` | `@[You] 📍 RX in Norwich \| 🐸 (2 hops) 〰️ SNR 7.5dB 📶 RSSI -85dBm \| 📏 34km` |

`test` gives where the bot heard you (`default_location`), the hop count, the signal report and how far away you are.

- The distance is a straight line from your advertised position to the bot's, found the same way as for `!dist`. It is left out when either position isn't known. It uses km, or miles with `dist_miles = true`.
- With no `default_location` set, the reply starts `Test OK` instead of `RX in …`.

| Emoji | Meaning |
|-------|---------|
| 📍 | Where the bot heard you |
| 🐸 | Hops: how many repeaters your message jumped through |
| 〰️ | SNR, signal-to-noise ratio: how clearly the signal stood out from the noise |
| 📶 | RSSI, received signal strength |
| 📏 | Straight-line distance from you to the bot |

With `use_emoji = false` the reply is plain text: `RX in Norwich | (2 hops) SNR 7.5dB RSSI -85dBm | 34km`.
- The hop count comes from the message itself. The path addresses aren't shown (use `!path`), because a long path makes the reply too long for one message.
- SNR and RSSI come from the message when the radio reports them. Otherwise they come from the radio's receive log for the same packet. Values the radio doesn't report are left out.
- A message that came by a direct route shows `(direct route)`.

### Path (!path)

| Command | Reply |
|---------|-------|
| `!path` | `@[You] 🛤️ 3 hops: a1 Norwich Cath › b2 › c3` |

Alias: `!trace`.

`!path` lists the repeaters your message came through, first repeater first. Each repeater shows as the short hash of its public key that MeshCore puts in the path.

- **Names:** when exactly one repeater in the bot radio's contact list has a key starting with that hash, its name is added (first 12 characters, `PATH_NAME_CHARS` in `run_bot.py`). A hash that matches no repeater, or several, stays as hex, because the bot can't be sure which one it was. Companion contacts are skipped because they don't repeat. The bot reads the contact list at startup and re-reads it every 5 minutes if the radio says it changed.
- **Long paths:** if the path doesn't fit in one message, the names are dropped first. If it still doesn't fit, the last repeaters become `+2 more`.
- **Other replies:** `0 hops, heard directly` means no repeater was involved. A message sent by direct route doesn't carry its path, so the reply says so.

`!path` shows the route the message already took. It doesn't send a MeshCore trace packet.

### Distance (!dist)

| Command | Reply |
|---------|-------|
| `!dist` | `@[You] 📏 You ›15km› a1 ›?› b2 ›5.0km› Bot \| 20km+, 1 of 3 legs unknown \| 34km direct` |

`!dist` takes the same path as `!path` and shows how far each leg is, from you, through each repeater, to the bot:

- **Legs:** `›15km›` is the straight-line distance between two points. `›?›` means one end has no known position.
- **Total:** the sum of the known legs. A `+` means some legs are unknown, so the real total is longer.
- **Direct:** the straight line from you to the bot, when both positions are known. Comparing it with the total shows how far the path wandered.
- **Long paths:** if the reply doesn't fit in one message, the leg list is dropped first, then the direct line.
- **Units:** km by default. Set `dist_miles = true` for miles.

Where positions come from:

| Point | Position |
|-------|----------|
| You | The location in your advert. In a DM the bot matches your key. In a channel it matches your name, and only if exactly one contact has it |
| Repeater | The location in its advert, when exactly one repeater in the bot's contacts matches the hash, as for `!path` names |
| Bot | The location set on the bot's radio, else `default_location` if it is one of your `[locations]` |

A node only has a position if its owner has set one and it is included in its adverts. Many companions and some repeaters leave it out. MeshCore sends 0,0 for "no position", which the bot treats as unknown. Distances are straight lines between the advertised points, not the path the radio waves took.

### Weather (!wx, !wxh, !wxf)

| Command | Reply |
|---------|-------|
| `!wx` / `!wx <place>` | Current weather |
| `!wxh` / `!wxh <place>` | Hour-by-hour outlook for the next few hours |
| `!wxf` / `!wxf <place>` | 3-day forecast |

```
🌙 Norwich 23:00 Clear 🌡️14°C (feels 13°) 💨NW 6mph gust 15 ☔0% 💧humidity 73%
🕒 Norwich: 23h ☁️13° ☔10% | 00h ☁️13° ☔20% | 01h 🌧️12° ☔30% | 02h 🌧️11° ☔40%
📅 Norwich: Wed ☁️ 17/8° ☔10% | Thu 🌧️ 18/8° ☔60% | Fri ☀️ 19/8° ☔5%
```

| Symbol | Meaning |
|--------|---------|
| 🌡️ 14°C | Air temperature |
| (feels 13°) | Feels-like temperature, after wind chill and humidity |
| 💨 NW 6mph | Wind direction (where it blows from) and average speed |
| gust 15 | Peak gust speed, same units |
| ☔ 0% | Chance of rain |
| 💧 humidity 73% | Relative humidity |
| 17/8° | Day high / night low |
| 23h | Hour of the day, 24-hour clock (23h = 23:00) |

Set `use_emoji = false` in `config.toml` for plain text:

```
Norwich 23:00 | Clear | 14C feels 13C | Wind NW 6mph gust 15 | Rain 0% | Humidity 73%
Norwich: 23h Partly cloudy 13C 10% | 00h Cloudy 13C 20% | 01h Light rain 12C 30%
```

If the place can't be found, the bot doesn't reply. The failed lookup is still logged.

**Without a Met Office API key**, the bot ignores `!wx`, `!wxh` and `!wxf` from everyone (admins included), leaves them out of `!help`, and leaves `{wx}`, `{wxh}` and `{wxf}` blank in scheduled messages. A scheduled message that ends up empty is skipped. The startup log warns when no key is found.

#### Hourly outlook (!wxh)

`!wxh` adds hours until it reaches `wxh_hours` or runs out of message space. With emojis and a short sender name, about 4 hours fit in one message. To cover a longer period, space the hours out with `wxh_step_hours`:

| `wxh_step_hours` | 4 entries cover |
|------------------|-----------------|
| `1` | The next 4 hours |
| `2` | The next 8 hours |
| `3` | The next 12 hours |

`!wxh` uses the same Met Office hourly data as `!wx`, so a `!wx` and a `!wxh` for the same place within 30 minutes cost one API call.

### Weather warnings (!warn)

| Command | Reply |
|---------|-------|
| `!warn` / `!warn <place>` | Met Office weather warnings for the region, then alerts if they change |
| `!warn <region code>` | Warnings for a region, for example `!warn nw` or `!warn uk` |

Alias: `!warnings`.

```
⚠️ East of England: 🟠💨 Wind Thu 18:00-Fri 12:00 | 🟡🌧️ Rain Sat 06:00-21:00
✅ No weather warnings for East of England
```

| Symbol | Meaning |
|--------|---------|
| 🔴 🟠 🟡 | Red, amber or yellow warning. Red is listed first |
| 🌧️ 💨 ❄️ 🧊 ⛈️ ⚡ 🌫️ 🌡️ | Rain, wind, snow, ice, thunderstorms, lightning, fog, extreme heat |
| `Thu 18:00-Fri 12:00` | When the warning is valid |
| `to Fri 12:00` | The warning has already started |
| `+2 more` | More warnings than fit in one message |

Warnings that have ended are left out. The data is the Met Office warnings RSS feed, which is free, needs no key and doesn't count toward the DataHub call budget. Each feed is cached for 10 minutes (`warn_cache_sec`).

#### Warning alerts

After someone sends `!warn`, the bot keeps checking that region for 24 hours (`warn_watch_hours`). It posts to the same channel or DM whenever the warnings change:

```
🔔 ⚠️ East of England: 🔴💨 Wind Thu 18:00-Fri 12:00 | 🟡🌧️ Rain Sat 06:00-21:00
🔔 ✅ No weather warnings for East of England
```

- A post goes out when a warning is added, its level or times change, or it is cancelled early. A warning that just runs out isn't posted.
- Nothing is posted unless someone has sent `!warn`. A scheduled `{warn}` message doesn't start alerts.
- Another `!warn` for the same region in the same place restarts the 24 hours.
- The bot checks every 60 seconds (`warn_watch_tick_sec`) and still uses the 10-minute cache, so a change posts up to about 11 minutes after the Met Office publishes it.
- Up to 10 region and channel pairs are watched at once (`warn_watch_max`). Further `!warn` requests still get a reply but don't start a watch.
- While the bot is muted, changes are held back and posted when the mute ends.
- Watches are kept in memory, so restarting the bot ends them.

The Met Office regenerates the feed about every 5 minutes, but its content only changes when a warning is issued or updated.

#### Regions

The Met Office issues warnings for 16 regions. The bot finds the region for a place by looking up the nearest postcode on postcodes.io. With no place it uses `default_location`. You can also give a region code:

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

A place with no postcode within 2 km (for example out at sea) gets no reply. A place the bot can't match to a region gets the whole UK list.

### Sun and moon (!sun, !moon)

| Command | Reply |
|---------|-------|
| `!sun` / `!sun <place>` | Sunrise, sunset and hours of daylight today |
| `!moon` | Moon phase, how much is lit, and the next full and new moon |

Aliases: `!sunrise`, `!sunset`.

```
Norwich Thu 24 Sep 🌅 06:43 🌇 18:50 ☀️ 12h06m daylight
🌔 Waxing gibbous, 95% lit | 🌕 Full Sat 26 Sep | 🌑 New Sat 10 Oct
```

The bot works these out itself, so they need no internet except to look up a place name.

Sunrise and sunset are for today in `timezone` and are accurate to about a minute. They are when the top of the sun meets a flat horizon, so hills and buildings make the real times a little different.

| Emoji | Phase | Emoji | Phase |
|-------|-------|-------|-------|
| 🌑 | New moon | 🌕 | Full moon |
| 🌒 | Waxing crescent | 🌖 | Waning gibbous |
| 🌓 | First quarter | 🌗 | Last quarter |
| 🌔 | Waxing gibbous | 🌘 | Waning crescent |

New moon, first quarter, full moon and last quarter are shown for about a day either side of the exact time. Full and new moon dates are accurate to within an hour, so a phase that falls just before or after midnight can show the wrong day.

### Aurora (!aurora)

Alias: `!solar`.

`!aurora` shows how disturbed the Earth's magnetic field is over the UK, from [AuroraWatch UK](https://aurorawatch.lancs.ac.uk/) at Lancaster University.

```
🟢 Green: No significant activity | 11nT now, 62nT peak 24h | AuroraWatch UK
🟠 Amber: Amber alert: possible aurora | 131nT now, 213nT peak 24h | AuroraWatch UK
```

| Part | Meaning |
|------|---------|
| 🟢 🟡 🟠 🔴 | Alert level: green, yellow (from 50 nT), amber (from 100 nT), red (from 200 nT) |
| `11nT now` | Disturbance so far this hour, in nanotesla |
| `62nT peak 24h` | Highest hourly disturbance in the last 24 hours |

A disturbed field also disturbs the ionosphere, which makes HF unreliable. MeshCore runs on UHF (868 MHz), which doesn't use the ionosphere, so the mesh is barely affected. `!aurora` is mainly for HF operators and aurora watchers.

The AuroraWatch UK API terms: non-commercial use only, every reply credits AuroraWatch UK, and no more than one request every 3 minutes. The bot caches for 5 minutes (`aurora_cache_sec`) and never less than 3, sends a `Referer` header pointing at this repo, and uses AuroraWatch UK's level names unchanged.

### Air quality and pollen (!aq, !pollen)

| Command | Reply |
|---------|-------|
| `!aq` / `!aq <place>` | Air quality index and main pollutants now |
| `!pollen` / `!pollen <place>` | Pollen forecast for the next 24 hours |

Alias: `!air` = `!aq`.

```
🟢 Norwich air: Fair (EAQI 22) | PM2.5 5 PM10 9 NO2 7 O3 63 µg/m³ | Open-Meteo
🌼 Norwich pollen 24h: Grass 45 Moderate | Alder 3 grains/m³ | Open-Meteo
```

`!aq` gives the European Air Quality Index (EAQI) and the main pollutants in µg/m³:

| EAQI | Band | | Pollutant | What it is |
|------|------|-|-----------|------------|
| 0-20 | 🟢 Good | | PM2.5 | Fine particles |
| 20-40 | 🟢 Fair | | PM10 | Coarse particles |
| 40-60 | 🟡 Moderate | | NO2 | Nitrogen dioxide, mostly traffic |
| 60-80 | 🟠 Poor | | O3 | Ozone |
| 80-100 | 🔴 Very poor | | | |
| over 100 | 🟣 Extremely poor | | | |

This is the European index, not the UK's 1 to 10 DAQI, so the numbers won't match UK-AIR. If the reply is too long, the pollutants are dropped and the index stays.

`!pollen` gives the highest count of each pollen type over the next 24 hours, in grains/m³, highest first:

- **Types:** grass, birch, alder, mugwort and ragweed. Types with no pollen are left out.
- **Levels:** grass gets a level from the Met Office scale: Low, Moderate from 30, High from 50, Very high from 150. The other types show the count only. Add thresholds for them with `pollen_levels`.
- **Out of season:** pollen is only forecast during the pollen season (roughly spring and summer). Outside it, the reply is `None forecast`.

Both come from the [Open-Meteo](https://open-meteo.com/) air quality API (European CAMS model). It is free for non-commercial use, needs no key, and the data is CC BY 4.0, so every reply credits Open-Meteo. One request covers both commands for a place, cached for an hour (`air_cache_sec`).

### Radio conditions (!hf, !vhf, !uhf)

| Command | Reply |
|---------|-------|
| `!hf` | HF band conditions for now (day or night), solar flux, K and A index, noise |
| `!vhf` | 6m, 4m and 2m E-skip, VHF aurora and tropo |
| `!uhf` / `!uhf <place>` | UHF tropospheric refraction now and the best in the next 24 hours |

Aliases: `!bands` = `!hf`, `!tropo` = `!uhf`.

```
📻 HF night: 80-40m🟢 30-20m🟢 17-15m🟡 12-10m🔴 | ☀️SFI 112 🧲K2 A19 | 🔊S1-S2 | N0NBH
📡 VHF: 6m Es🟢 50MHz ES 4m Es🔴 2m Es🔴 Aurora🔴 | Tropo ➖Normal | N0NBH, Open-Meteo
📶 Norwich UHF tropo: ➖Normal now (-38 N/km) | next 24h similar | Open-Meteo
📶 Norwich UHF tropo: 🔼Slightly enhanced now (-65 N/km) | 24h best ⏫Enhanced Thu 06h (-89) | Open-Meteo
```

| Emoji | Meaning |
|-------|---------|
| 🟢 🟡 🔴 | HF band Good, Fair, Poor. VHF: 🟢 open (with what N0NBH reports), 🔴 closed |
| ☀️ SFI | Solar flux index. Higher is better for the upper HF bands |
| 🧲 K / A | Geomagnetic K and A index. Lower is quieter and better. K is 0 to 9 |
| 🔊 | Expected noise level (S-units), shown when it fits |
| 🔽 ➖ 🔼 ⏫ 🚀 | Tropo below normal, normal, slightly enhanced, enhanced, ducting likely |

With `use_emoji = false`, the reports use words instead: `80-40m Good | 30-20m Good | ...`, `6m Es Closed | ...`, `Tropo Normal`.

**HF and VHF** come from the solar data feed by Paul, N0NBH, at [hamqsl.com](https://www.hamqsl.com/solar.html). The author asks for credit and no more than one fetch an hour, so the bot caches it for an hour (`hf_cache_sec`) and never less. `!hf` shows N0NBH's day or night figures depending on whether the sun is up at `default_location`. `!vhf` shows sporadic-E over Europe and northern hemisphere VHF aurora from N0NBH, plus the tropo level at `default_location` if it fits.

**UHF** reach, including MeshCore's own 868 MHz, depends on the lower atmosphere, not the sun. Beyond line of sight the main effect is tropospheric refraction and ducting under temperature inversions. N0NBH has no UHF figures, so the bot works it out from the Open-Meteo weather forecast:

1. It takes the temperature, humidity and pressure at 2 m and at the 925 hPa level, about 700 m up.
2. It works out the radio refractivity N at both heights, using the ITU-R P.453 formula.
3. It divides the difference by the height between them, giving the refractivity gradient in N-units per km.

A normal atmosphere is about -40 N/km. More negative means signals bend further back towards the ground and travel further.

| Gradient (N/km) | Level | Meaning |
|-----------------|-------|---------|
| above 0 | 🔽 Below normal | Signals bend up, range shorter than usual |
| 0 to -60 | ➖ Normal | Standard atmosphere |
| -60 to -79 | 🔼 Slightly enhanced | Some extra range |
| -79 to -157 | ⏫ Enhanced | Super-refraction (ITU-R), clearly longer paths |
| below -157 | 🚀 Ducting likely | Ducting (ITU-R), paths of hundreds of km possible |

The -79 and -157 limits are the ITU-R ones. -60 is the bot's own early hint. The 700 m layer averages out thin inversions, so real ducting near the ground can be stronger than the figure suggests. Treat it as a guide, not a measurement. Ducting is most likely on calm nights and mornings under high pressure, and over the sea.

`!uhf` uses the same free Open-Meteo terms as `!aq` and caches each place for an hour (`tropo_cache_sec`). Places above about 700 m, where the ground is close to the 925 hPa level, get `no forecast data`.

### Fun commands (!roll, !flipacoin, !eightball)

| Command | Reply |
|---------|-------|
| `!roll` | `🎲 1d6: 4` |
| `!roll d20` | `🎲 1d20: 17` |
| `!roll 2d6` | `🎲 2d6: 3 + 5 = 8` |
| `!roll 3d8+2` | `🎲 3d8+2: 5 + 3 + 2 + 2 = 12` |
| `!flipacoin` | `🪙 Heads` or `🪙 Tails` |
| `!eightball <question>` | `🎱 Signs point to yes.` |

Aliases: `!dice` = `!roll`, `!flip` and `!coin` = `!flipacoin`, `!8ball` = `!eightball`.

- `NdS` means N dice with S sides. Leave out N for one die: `d20`. A single number is the sides: `!roll 20` = `!roll d20`.
- Add or subtract a fixed amount: `2d6+3`, `1d20-1`.
- Limits: 1 to 10 dice, 2 to 1000 sides (`roll_max_dice`, `roll_max_sides`).
- `!eightball` needs a question. It picks from 19 answers: 8 yes, 5 unsure and 6 no. Change them with `eightball_answers`.
- Dice, coin and eight ball use Python's `SystemRandom`, which draws on the operating system's random source.

### Admin commands

These only work in a **direct message** from a key in `admin_pubkeys`. Anyone else, and any channel message, gets no reply. They aren't listed in `!help`.

| Command | Reply |
|---------|-------|
| `!mute <minutes>` | Stops all replies and scheduled messages for 1 to 1440 minutes: `🔇 Muted for 30m, until 20:30` |
| `!mute` | Shows whether the bot is muted and for how long |
| `!mute 0` / `!unmute` | Ends the mute early |
| `!say <ch> <text>` | Posts the text to a channel slot as the bot: `!say 1 Net starts 20:00` |
| `!stats` | Commands served, messages heard and sent, rate limited commands, Met Office calls today, radio battery |
| `!uptime` | How long the bot and the Pi have been running |

Why DMs only: channel messages carry only the sender's display name, not their key, so the bot can't tell an admin apart in a channel.

To make yourself an admin, add the first 12 hex characters of your public key to `admin_pubkeys` in `config.toml`. Case is ignored and a full key works too.

#### Mute and say

`!mute` is for quieting the bot during a net or an event. While muted, the bot ignores every command from everyone except admin DMs, with no reply and no "slow down" notices. Scheduled messages that fall due are skipped, not sent later. A new `!mute` replaces the old one. The mute is kept in memory, so restarting the bot ends it. Set the longest mute with `mute_max_minutes`.

`!say` posts to any channel slot, not only the ones in `channel_idxs`. The text is cut to `max_reply_bytes`. The reply `📢 Queued for ch1` means the message is in the send queue. If the radio then refuses it, for example because the slot has no channel, the failure is only logged.

#### Stats and uptime

```
📊 Cmds 42 (wx 20, ping 12, test 5) | Heard 310 | Sent 45 | Limited 3 | WX API 18/300 | 🔋4.02V
⏱️ Bot up 3d 4h 12m (since Mon 21 Sep 09:14) | System up 12d 3h 40m
```

| Field | Meaning |
|-------|---------|
| Cmds | Commands answered, with the busiest ones. The list shortens to fit |
| Heard | Messages heard on the listening channels and in DMs |
| Sent | Messages sent by the bot. `Failed` appears if any sends failed |
| Limited | Commands dropped by a rate limit |
| WX API | Met Office calls today out of `wx_daily_call_budget` |
| 🔋 | Radio battery voltage, if the radio reports it |

Counts start at 0 each time the bot starts. `System up` comes from `/proc/uptime`, so it only shows on Linux.

### Rate limiting

Every command is checked against three limits. A command only runs when all three allow it.

| Limit | Default | Counted per |
|-------|---------|-------------|
| Per user | 3 per 60s | Sender name in channels, public key in DMs |
| Per channel | 8 per 60s | Channel, with one shared bucket for all DMs |
| Global | 20 per 60s | Whole bot |

A user who hits their limit gets one notice per window: `Slow down, try again in 42s`. Other limits drop the command without a reply. Radio sends are queued with at least 3 seconds between them.

Admin keys skip all limits in DMs. In channels admins get the normal limits, because the bot can't tell who they are. Channel users are tracked by name, so someone who changes their name gets a fresh per-user allowance, but the per-channel and global limits still apply.

---

## Settings reference

Put these in `config.toml`, in lower case, for example `channel_idxs = [1, 3]`. Anything you leave out keeps the built-in default.

The bot reads `config.toml` from the folder `run_bot.py` is in. To use another file, pass `--config /path/to/file.toml` or set `MESHPOTATO_CONFIG`.

| Setting | Default | Notes |
|---------|---------|-------|
| **Basics** | | |
| `metoffice_api_key` | none | Met Office key. `chmod 600 config.toml` if you use it |
| `serial_port` | `"/dev/ttyACM0"` | Overridden by `--port` |
| `channel_idxs` | `[1, 3]` | Channel slots the bot listens and replies on |
| `answer_dms` | `true` | Reply to direct messages |
| `timezone` | `"Europe/London"` | Used for the schedule and reply times |
| `log_level` | `"INFO"` | `"DEBUG"` also logs messages that aren't commands |
| `default_location` | `"Norwich"` | Used when no place is given |
| `[locations]` | Norwich, Cambridge, Ipswich… | Named places, such as `Norwich = [52.6278, 1.2983]`. Replaces the built-in list |
| **Replies** | | |
| `use_emoji` | `true` | `false` for plain text |
| `use_mph` | `true` | `false` for m/s |
| `dist_miles` | `false` | `true` to show `!dist` in miles |
| `max_reply_bytes` | `135` | MeshCore limits messages by bytes. Emojis take 4 to 7 bytes each |
| **Weather** | | |
| `wx_cache_sec` | `1800` | How long to reuse a forecast (30 minutes) |
| `wx_daily_call_budget` | `300` | Most Met Office calls per UTC day |
| `wxh_hours` | `6` | Most hours `!wxh` shows, if they fit |
| `wxh_step_hours` | `1` | Gap between `!wxh` entries in hours |
| `wxh_mention_reserve` | `25` | Bytes kept free for other text when `{wxh}` is used in a scheduled message |
| `warn_cache_sec` | `600` | How long to reuse the warnings feed (10 minutes) |
| `warn_watch_hours` | `24` | How long after a `!warn` the bot posts warning changes |
| `warn_watch_max` | `10` | Most region and channel pairs watched at once |
| `warn_watch_tick_sec` | `60` | How often watched regions are checked |
| **Other data** | | |
| `aurora_cache_sec` | `300` | Reuse AuroraWatch UK data (never less than 180) |
| `air_cache_sec` | `3600` | Reuse air quality and pollen for a place |
| `pollen_levels` | `{grass = [30, 50, 150]}` | Counts where Moderate, High and Very high start, per type |
| `hf_cache_sec` | `3600` | Reuse the N0NBH HF/VHF feed (never less than 3600) |
| `tropo_cache_sec` | `3600` | Reuse a place's UHF tropo forecast |
| **Fun** | | |
| `roll_max_dice` | `10` | Most dice per `!roll` |
| `roll_max_sides` | `1000` | Most sides per die |
| `eightball_answers` | 19 answers | List of `!eightball` replies |
| **Rate limits and admin** | | |
| `rate_limit_per_user` | `[3, 60]` | [max commands, seconds] |
| `rate_limit_per_channel` | `[8, 60]` | |
| `rate_limit_global` | `[20, 60]` | |
| `rate_limit_notify` | `true` | Send one "slow down" notice |
| `min_tx_gap_sec` | `3.0` | Gap between radio sends |
| `admin_pubkeys` | `[]` | 12-hex-character key prefixes that skip limits and can use admin commands in DMs |
| `mute_max_minutes` | `1440` | Longest `!mute` |
| `scheduled_messages` | built-in list | See [Scheduled messages](#scheduled-messages) |

A misspelled setting is logged and ignored. A value of the wrong type stops the bot at startup with a message naming the setting.

---

## Scheduled messages

The bot can post messages at set times. Out of the box it posts three to channel 1:

- `Morning WX {wx}` at 07:30 on weekdays
- the 3-day forecast `{wxf}` at 08:30 at weekends
- `Merry Christmas.` at 09:00 on 25 December

To change them, add one `[[scheduled_messages]]` block per message to `config.toml`. **Any block you add replaces the whole built-in list**, so include every message you want. To turn scheduled messages off completely, add `scheduled_messages = []` near the top of `config.toml`, before any `[section]`.

```toml
[[scheduled_messages]]
name = "morning-wx"
time = "07:30"
days = ["mon", "tue", "wed", "thu", "fri"]
channel = 1
text = "Morning WX {wx}"
```

Each entry needs `text`, a target and one timing rule. `name` is optional and appears in the logs.

**Target**, one of:

- `channel = 1` sends to a channel slot
- `dm = "a1b2c3d4e5f6"` sends to a contact's public key prefix

**Timing**, one of:

| Rule | Example | Fires |
|------|---------|-------|
| `time` | `time = "07:30"` | Every day at 07:30 |
| `time` + `days` | `time = "07:30"`, `days = ["mon", "fri"]` | Those days only |
| `at` | `at = "2026-12-25 09:00"` | Once |
| `at` (no year) | `at = "12-25 09:00"` | Every year on that date |
| `every_minutes` | `every_minutes = 360`, `start = "00:00"` | 00:00, 06:00, 12:00, 18:00 |

**Tokens** in `text` are filled in when the message is sent. Add `:place` to use somewhere other than `default_location`:

| Token | Becomes |
|-------|---------|
| `{time}` | `07:30` |
| `{date}` | `Wed 23 Sep` |
| `{wx}` / `{wx:Cambridge}` | Current weather |
| `{wxh}` / `{wxh:Ipswich}` | Hourly outlook |
| `{wxf}` / `{wxf:Ipswich}` | 3-day forecast |
| `{warn}` / `{warn:Ipswich}` | Weather warnings for the region |
| `{sun}` / `{sun:Ipswich}` | Sunrise, sunset and daylight |
| `{moon}` | Moon phase |
| `{aurora}` | Aurora alert level |
| `{aq}` / `{aq:Ipswich}` | Air quality |
| `{pollen}` / `{pollen:Ipswich}` | Pollen forecast |
| `{hf}` | HF band conditions |
| `{vhf}` | VHF E-skip, aurora and tropo |
| `{uhf}` / `{uhf:Ipswich}` | UHF tropo |

More examples:

```toml
[[scheduled_messages]]
name = "commute"
time = "07:00"
days = ["mon", "tue", "wed", "thu", "fri"]
channel = 1
text = "{wxh}"

[[scheduled_messages]]
name = "weekend-fcst"
time = "08:30"
days = ["sat", "sun"]
channel = 1
text = "{wxf}"

[[scheduled_messages]]
name = "morning-warnings"
time = "07:00"
channel = 1
text = "{warn}"

[[scheduled_messages]]
name = "beacon"
every_minutes = 360
start = "00:00"
channel = 3
text = "WX bot online {time}. Send !help for commands."

[[scheduled_messages]]
name = "xmas"
at = "12-25 09:00"
channel = 1
text = "Merry Christmas."
```

Rules:

- Scheduled messages skip the rate limits but still go through the send queue.
- A slot fires up to 2 minutes late (`schedule_grace_sec`), for example after a restart. Slots missed by more than that are skipped. No backlog is sent on startup.
- `every_minutes` must be 5 or more.
- If a `{wx:place}` can't be found, the message is still sent, with `WX: 'place' not found` in place of the weather.
- A scheduled `{warn}` doesn't start warning alerts. Only someone sending `!warn` does.
- Entries with a mistake are logged at startup and skipped. The rest still run.
- The Pi needs the correct time. Check with `timedatectl`.

---

## Files in this repo

| File | Purpose |
|------|---------|
| `run_bot.py` | The bot |
| `install_mesh_potato_bot_service.sh` | Installs the bot as a systemd service |
| `config.example.toml` | Example settings. Copy it to `config.toml` |
| `channel_list.py` | Lists the channels on your radio, with their slot numbers |
| `tests/` | Tests for the bot, run with pytest |
| `.gitignore` | Keeps your API key, `config.toml` and Python caches out of git |

---

## To do

- [ ] **`!tide <place>`**: UK tide times (next high and low water, with heights) from the [ADMIRALTY UK Tidal API](https://www.admiralty.co.uk/access-data/apis), run by the UK Hydrographic Office.
  - Needs a free Discovery tier key, stored like the Met Office key. Check the tier's station count, days of predictions and call limits on the portal before building.
  - Match the place to the nearest tidal station, and don't reply for places far inland.
  - Cache predictions per station for several hours, because tide predictions don't change during the day.
  - Credit ADMIRALTY / UKHO in the reply if the licence asks for it.

---

## Credits

- Built on the [meshcore_py](https://github.com/meshcore-dev/meshcore_py) examples `serial_pingbot.py` and `serial_rss_bot.py`
- Weather: Met Office Weather DataHub, Site Specific API
- Weather warnings: Met Office warnings RSS feed
- Geocoding: postcodes.io
- Aurora alerts: [AuroraWatch UK](https://aurorawatch.lancs.ac.uk/), Lancaster University
- Air quality and pollen: [Open-Meteo](https://open-meteo.com/) (CC BY 4.0), from the Copernicus Atmosphere Monitoring Service (CAMS)
- HF and VHF conditions: Paul, N0NBH, [hamqsl.com](https://www.hamqsl.com/solar.html)
- UHF tropo: worked out from the [Open-Meteo](https://open-meteo.com/) weather forecast (CC BY 4.0) with the ITU-R P.453 refractivity formula

## License

MIT, see [LICENSE](LICENSE).
