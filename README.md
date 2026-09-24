# MeshPotato 🥔: MeshCore WX, Ping, Bot

A MeshCore chat bot for a Raspberry Pi running Arch Linux ARM. It answers ping and test commands, posts Met Office weather and weather warnings, gives sunrise, sunset and moon phase, reports its own stats, rate limits spam and sends scheduled messages. It runs as a systemd service and starts on boot.

Files in this repo:

| File | Purpose |
|------|---------|
| `run_bot.py` | The bot, with emoji weather replies |
| `install_mesh_potato_bot_service.sh` | Installs the bot as a systemd service |
| `config.example.toml` | Example settings. Copy it to `config.toml` to change settings |
| `tests/` | Tests for the bot, run with pytest |
| `README.md` | This guide |
| `.gitignore` | Keeps your API key, `config.toml` and Python caches out of git |

---

## Requirements

- Raspberry Pi with Arch Linux ARM (the default user is `alarm`)
- A MeshCore companion radio on USB serial firmware
- Python 3.11 or newer (3.10 works, but can't read `config.toml`)
- A free Met Office DataHub API key
- Internet access for the Pi

---

## 1. What the bot does

### Commands

Send these in a listening channel or as a direct message.

`ping` and `test` must be the whole message, with or without a leading `!`. The bot ignores "ping me later" or "test of the new antenna". Every other command needs the `!`, so normal chat that starts with `wx` or `help` doesn't trigger anything.

#### Ping and test (whole message, `!` optional)

| Command | Reply |
|---------|-------|
| `ping` | `@[You] Pong (2 hops)` |
| `test` | `@[You] Test OK (2 hops) SNR 7.5dB RSSI -85dBm` with hop count and signal report |

The hop count comes from the message itself. The path addresses aren't shown, because a long path makes the reply too long for one message. SNR and RSSI come from the message when the radio reports them. Otherwise they come from the radio's receive log for the same packet. A message that came by a direct route shows `(direct route)`. Values the radio doesn't report are left out.

#### Weather (`!` required)

| Command | Reply |
|---------|-------|
| `!wx` | Current weather for the default location |
| `!wx <location>` | Current weather for a location |
| `!wxh` / `!wxh <location>` | Hour-by-hour outlook for the next few hours |
| `!wxf` / `!wxf <location>` | 3-day forecast |

If the location can't be found, the bot doesn't reply. The failed lookup is still logged.

#### Warnings, sun and moon (`!` required)

| Command | Reply |
|---------|-------|
| `!warn` / `!warn <location>` | Met Office weather warnings for the region |
| `!warn <region code>` | Warnings for a region, for example `!warn nw` or `!warn uk` |
| `!sun` / `!sun <location>` | Sunrise, sunset and hours of daylight today |
| `!moon` | Moon phase, how much is lit, and the next full and new moon |

Short aliases: `!warnings` = `!warn`, `!sunrise` and `!sunset` = `!sun`.

#### Bot status (`!` required)

| Command | Reply |
|---------|-------|
| `!stats` | Commands served, messages heard and sent, rate limited commands, Met Office calls used today, radio battery |
| `!uptime` | How long the bot has been running, and how long the Pi has been up |

#### Fun and help (`!` required)

| Command | Reply |
|---------|-------|
| `!help` | Command list |
| `!roll` | `🎲 1d6: 4` |
| `!roll d20` | `🎲 1d20: 17` |
| `!roll 2d6` | `🎲 2d6: 3 + 5 = 8` |
| `!roll 3d8+2` | `🎲 3d8+2: 5 + 3 + 2 + 2 = 12` |
| `!flipacoin` | `🪙 Heads` or `🪙 Tails` |
| `!eightball <question>` | `🎱 Signs point to yes.` |

Short aliases: `!dice` = `!roll`, `!flip` and `!coin` = `!flipacoin`, `!8ball` = `!eightball`.

`!roll` rules:

- `NdS` means N dice with S sides. Leave out N for one die: `d20`.
- A single number is the number of sides: `!roll 20` is the same as `!roll d20`.
- Add or subtract a fixed amount: `2d6+3`, `1d20-1`.
- Limits: 1 to 10 dice, 2 to 1000 sides (`ROLL_MAX_DICE`, `ROLL_MAX_SIDES`).

`!eightball` needs a question. It picks from 19 answers: 8 yes, 5 unsure and 6 no. Set `eightball_answers` in `config.toml` to change them.

Dice, coin and eight ball use Python's `SystemRandom`, which draws on the operating system's random source.

The fun, warning, sun, moon and status commands count toward the same rate limits as the weather commands.

`<location>` accepts:

- A name from `LOCATIONS`: `!wx cambridge` (not case sensitive)
- A UK postcode: `!wx NR1 3JU`
- The first half of a postcode: `!wx NR1`
- A UK town or village: `!wx Cromer`
- Latitude and longitude: `!wx 52.63,1.30`

Postcodes and place names are looked up on postcodes.io, which is free and needs no key. Its place names cover Great Britain only. For Northern Ireland, use a postcode: `!wx BT1 1AA`. When several places share a name, the bot picks the biggest one, so `!wx Brighton` is the city rather than the hamlet in Cornwall.

### Example replies

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

### Hourly outlook (!wxh)

`!wxh` lists the coming hours with weather, temperature and chance of rain. It adds hours until it reaches `WXH_HOURS` or runs out of message space. With emojis and a short sender name, about 4 hours fit in one message.

To cover a longer period in one message, space the hours out with `WXH_STEP_HOURS`:

| `WXH_STEP_HOURS` | 4 entries cover |
|------------------|-----------------|
| `1` | The next 4 hours |
| `2` | The next 8 hours |
| `3` | The next 12 hours |

`!wxh` uses the same Met Office hourly data as `!wx`, so a `!wx` and a `!wxh` for the same place within 30 minutes cost one API call.

### Weather warnings (!warn)

`!warn` reads the Met Office warnings RSS feed. It is free, needs no key and doesn't count toward the Met Office DataHub call budget. Each feed is cached for 10 minutes (`WARN_CACHE_SEC`).

```
⚠️ East of England: 🟠💨 Wind Thu 18:00-Fri 12:00 | 🟡🌧️ Rain Sat 06:00-21:00
✅ No warnings for East of England
```

| Symbol | Meaning |
|--------|---------|
| 🔴 🟠 🟡 | Red, amber or yellow warning. Red is listed first |
| 🌧️ 💨 ❄️ 🧊 ⛈️ ⚡ 🌫️ 🌡️ | Rain, wind, snow, ice, thunderstorms, lightning, fog, extreme heat |
| `Thu 18:00-Fri 12:00` | When the warning is valid |
| `to Fri 12:00` | The warning has already started |
| `+2 more` | More warnings than fit in one message |

Warnings that have ended are left out.

The Met Office issues warnings for 16 regions. The bot finds the region for a location by looking up the nearest postcode on postcodes.io. With no location it uses `DEFAULT_LOCATION`. You can also give a region code:

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

The bot works these out itself, so they need no internet except to look up a place name.

```
Norwich Thu 24 Sep 🌅 06:43 🌇 18:50 ☀️ 12h06m daylight
🌔 Waxing gibbous, 95% lit | 🌕 Full Sat 26 Sep | 🌑 New Sat 10 Oct
```

Sunrise and sunset are for today in `TIMEZONE` and are accurate to about a minute. They are when the top of the sun meets a flat horizon, so hills and buildings make the real times a little different.

The moon emoji matches the phase as seen from the UK:

| Emoji | Phase |
|-------|-------|
| 🌑 | New moon |
| 🌒 | Waxing crescent |
| 🌓 | First quarter |
| 🌔 | Waxing gibbous |
| 🌕 | Full moon |
| 🌖 | Waning gibbous |
| 🌗 | Last quarter |
| 🌘 | Waning crescent |

New moon, first quarter, full moon and last quarter are shown for about a day either side of the exact time. The in-between phases fill the days between. Full and new moon dates are accurate to within an hour, so a phase that falls just before or after midnight can show the wrong day.

### Stats and uptime (!stats, !uptime)

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
| WX API | Met Office calls today out of `WX_DAILY_CALL_BUDGET` |
| 🔋 | Radio battery voltage, if the radio reports it |

Counts start at 0 each time the bot starts. `System up` comes from `/proc/uptime`, so it only shows on Linux.

### Rate limiting

Every command is checked against three limits. A command only runs and counts when all three allow it.

| Limit | Default | Key |
|-------|---------|-----|
| Per user | 3 per 60s | Sender name in channels, public key in DMs |
| Per channel | 8 per 60s | Channel index, or one shared bucket for DMs |
| Global | 20 per 60s | Whole bot |

A user who hits their limit gets one notice per window: `Slow down, try again in 42s`. Other limits drop the command without a reply. Radio sends are queued with at least 3 seconds between them.

Public keys in `ADMIN_PUBKEYS` skip all limits, **in direct messages only**. Channel messages carry only the sender's display name, not their key, so the bot can't tell an admin apart in a channel, and admins get the normal limits there. Keys are matched on their first 12 hex characters, ignoring case, so a full key works too.

Because channel users are tracked by name, someone who changes their name gets a fresh per-user allowance. The per-channel and global limits still apply.

### Scheduled messages

The bot sends messages at set times from the `SCHEDULED_MESSAGES` list. Scheduled messages skip the rate limits but still go through the send queue.

---

## 2. Get a Met Office API key

1. Register at https://datahub.metoffice.gov.uk/
2. Subscribe to the **Site Specific** (Global Spot) free plan.
3. Copy the API key. It is about 1670 characters long, so copy all of it.

The free plan allows 360 calls a day. To stay inside that, the bot:

- caches each forecast for 30 minutes (`WX_CACHE_SEC`)
- stops calling the Met Office after 300 calls in a UTC day (`WX_DAILY_CALL_BUDGET`). Cached forecasts are still served. New lookups reply `WX: daily quota used, try tomorrow` until midnight UTC.

The call count is kept in memory, so it starts again at 0 when the bot restarts.

---

## 3. Install on Arch Linux ARM

### 3.1 Packages

```bash
sudo pacman -Syu
sudo pacman -S --needed python python-pip tzdata git
```

### 3.2 The meshcore Python package

Option A, system-wide (simplest on a dedicated Pi):

```bash
pip install --user --break-system-packages meshcore
```

Option B, a virtual environment (cleaner):

```bash
python -m venv ~/meshbot
~/meshbot/bin/pip install meshcore
```

If you use option B, add `--python ~/meshbot/bin/python` to the install command in section 6.

The Met Office code only uses modules built into Python. No other packages are needed.

### 3.3 Serial port access

On Arch the radio's port belongs to the `uucp` group.

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

### 3.4 Clone the repo to the Pi

Clone the repo into your home folder as the `alarm` user:

```bash
cd ~
git clone https://github.com/swooningfish/MeshPotato.git
cd MeshPotato
```

This clones the repo into `~/MeshPotato`. The service runs the bot from this folder, so keep it here after you install. Run every later command in this guide from `~/MeshPotato`.

Make the installer executable:

```bash
chmod +x install_mesh_potato_bot_service.sh
```

If the scripts fail with `bad interpreter` or `$'\r': command not found`, they have Windows line endings. Fix them with:

```bash
sed -i 's/\r$//' install_mesh_potato_bot_service.sh run_bot.py
```

To update the bot later:

```bash
cd ~/MeshPotato
git pull
sudo systemctl restart meshcore-meshpotato-bot
```

### 3.5 Store the API key

The service doesn't read `~/.bashrc`, so keep the key in `config.toml` or in a key file.

**Option A, in `config.toml`** (one place for everything):

```bash
cp config.example.toml config.toml        # if you haven't already
nano config.toml                          # uncomment metoffice_api_key, paste the key between the quotes
chmod 600 config.toml
```

Paste the whole key on one line. `config.toml` is ignored by git, so the key won't be committed.

**Option B, in a key file:**

```bash
mkdir -p ~/.config/meshcore
nano ~/.config/meshcore/metoffice_key     # paste the key, save
chmod 600 ~/.config/meshcore/metoffice_key
```

The bot looks for the key in this order and uses the first it finds:

1. `METOFFICE_API_KEY` environment variable
2. `metoffice_api_key` in `config.toml`
3. The file named in `METOFFICE_KEY_FILE`
4. `~/.config/meshcore/metoffice_key`
5. `metoffice_key.txt` in the same folder as the script

The startup log says which one it used.

If `config.toml` holds a key, the installer sets the file to mode 600. Otherwise, if `METOFFICE_API_KEY` is set in your shell when you run the installer, it writes the key file for you.

---

## 4. Configure the bot

Put your settings in `config.toml`, not in `run_bot.py`. `config.toml` is ignored by git, so `git pull` never clashes with your changes.

```bash
cp config.example.toml config.toml
nano config.toml
```

Uncomment and change only the lines you need. Anything you leave out keeps the default from `run_bot.py`. Setting names are the ones below in lower case, for example `channel_idxs = [1, 3]`.

The bot reads `config.toml` from the folder `run_bot.py` is in. To use another file, pass `--config /path/to/file.toml` or set `MESHPOTATO_CONFIG`. A misspelled setting is logged and ignored. A value of the wrong type, such as `use_emoji = "yes"`, stops the bot at startup with a message naming the setting.

`config.toml` needs Python 3.11 or newer. On 3.10 the bot runs on the defaults and stops with an error if a `config.toml` is present.

| Setting | Example (`config.toml`) | Notes |
|---------|-------------------------|-------|
| `metoffice_api_key` | `"paste-your-key-here"` | Met Office key. See section 3.5. `chmod 600 config.toml` if you use it |
| `serial_port` | `"/dev/ttyACM0"` | Overridden by `--port` |
| `channel_idxs` | `[1, 3]` | Channel slots the bot listens and replies on |
| `answer_dms` | `true` | Reply to direct messages |
| `timezone` | `"Europe/London"` | Used for the schedule and reply times |
| `log_level` | `"INFO"` | `"DEBUG"` also logs messages that aren't commands |
| `default_location` | `"Norwich"` | Used by `!wx` with no location |
| `[locations]` | `Norwich = [52.6278, 1.2983]` | Named shortcuts. Replaces the built-in list |
| `use_emoji` | `true` | `false` for plain text |
| `use_mph` | `true` | `false` for m/s |
| `roll_max_dice` | `10` | Most dice per `!roll` |
| `roll_max_sides` | `1000` | Most sides per die |
| `eightball_answers` | 19 answers | List of `!eightball` replies |
| `wxh_hours` | `6` | Most hours `!wxh` shows, if they fit |
| `wxh_step_hours` | `1` | Gap between `!wxh` entries in hours |
| `wxh_mention_reserve` | `25` | Bytes kept free for other text when `{wxh}` is used in a scheduled message |
| `warn_cache_sec` | `600` | How long to reuse the Met Office warnings feed (10 minutes) |
| `max_reply_bytes` | `135` | MeshCore limits messages by bytes. Emojis take 4 to 7 bytes each |
| `wx_cache_sec` | `1800` | How long to reuse a forecast (30 minutes) |
| `wx_daily_call_budget` | `300` | Most Met Office calls per UTC day |
| `rate_limit_per_user` | `[3, 60]` | [max commands, seconds] |
| `rate_limit_per_channel` | `[8, 60]` | |
| `rate_limit_global` | `[20, 60]` | |
| `rate_limit_notify` | `true` | Send one "slow down" notice |
| `min_tx_gap_sec` | `3.0` | Gap between radio sends |
| `admin_pubkeys` | `["a1b2c3d4e5f6"]` | 12-hex-character key prefixes that skip limits in DMs |

### Scheduled messages

Each entry needs `text`, a target and one timing rule. Any `[[scheduled_messages]]` entry in `config.toml` replaces the whole built-in list.

Target:

- `channel = 1` sends to a channel slot
- `dm = "a1b2c3d4e5f6"` sends to a contact's public key prefix

Timing:

| Rule | Example | Fires |
|------|---------|-------|
| `time` | `"07:30"` | Every day at 07:30 |
| `time` + `days` | `"07:30"`, `["mon", "fri"]` | Those days only |
| `at` | `"2026-12-25 09:00"` | Once |
| `every_minutes` | `360`, `start = "00:00"` | 00:00, 06:00, 12:00, 18:00 |

Tokens filled in at send time:

| Token | Becomes |
|-------|---------|
| `{time}` | `07:30` |
| `{date}` | `Wed 23 Sep` |
| `{wx}` | Current weather, default location |
| `{wx:Cambridge}` | Current weather for Cambridge |
| `{wxh}` / `{wxh:Ipswich}` | Hourly outlook |
| `{wxf}` / `{wxf:Ipswich}` | 3-day forecast |
| `{warn}` / `{warn:Ipswich}` | Weather warnings for the region |
| `{sun}` / `{sun:Ipswich}` | Sunrise, sunset and daylight |
| `{moon}` | Moon phase |

Example:

```toml
[[scheduled_messages]]
name = "morning-wx"
time = "07:30"
days = ["mon", "tue", "wed", "thu", "fri"]
channel = 1
text = "Morning WX {wx}"

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
name = "beacon"
every_minutes = 360
start = "00:00"
channel = 3
text = "WX bot online {time}. Send !help for commands."

[[scheduled_messages]]
name = "xmas"
at = "2026-12-25 09:00"
channel = 1
text = "Merry Christmas."
```

If a scheduled `{wx:place}` can't be found, the message is still sent, with `WX: 'place' not found` in place of the weather.

Rules:

- A slot fires up to 2 minutes late (`SCHEDULE_GRACE_SEC`), for example after a restart. Slots missed by more than that are skipped. No backlog is sent on startup.
- `every_minutes` must be 5 or more.
- Entries with a mistake are logged at startup and skipped. The rest still run.

---

## 5. Test before installing the service

Check the Met Office side without the radio:

```bash
python run_bot.py --wx norwich
python run_bot.py --wxh norwich
python run_bot.py --wxf "NR1 3JU"
```

Check warnings, sun and moon the same way. Leave out the location to use `DEFAULT_LOCATION`:

```bash
python run_bot.py --warn
python run_bot.py --warn Cromer
python run_bot.py --sun NR1
python run_bot.py --moon
```

Run the full bot in the foreground (Ctrl+C to stop):

```bash
python run_bot.py --port /dev/ttyACM0
```

Look for these log lines:

```
Settings loaded from /home/alarm/MeshPotato/config.toml
Met Office API key loaded from /home/alarm/MeshPotato/config.toml (1670 chars)
Connected on /dev/ttyACM0
Scheduler running with 3 entries
Listening on channels [1, 3] and DMs
```

The first line only appears when you have a `config.toml`.

Send `ping`, `test`, `!wx`, `!roll 2d6` and `!help` from another node on channel 1 and check for the replies.

### Run the tests

The tests don't need the radio, the `meshcore` package or an API key:

```bash
sudo pacman -S --needed python-pytest
python -m pytest tests
```

Stop the foreground bot before installing the service. Only one program can open the serial port at a time.

---

## 6. Install as a service

From the folder with the files, as the `alarm` user (not root):

```bash
./install_mesh_potato_bot_service.sh
```

With options:

```bash
./install_mesh_potato_bot_service.sh --script run_bot.py --port /dev/ttyACM0
./install_mesh_potato_bot_service.sh --python ~/meshbot/bin/python      # venv install
```

| Option | Default                     | Purpose |
|--------|-----------------------------|---------|
| `--script` | `run_bot.py`                | Which bot file to run |
| `--port` | first `/dev/serial/by-id/*` | Serial port |
| `--python` | `python3` on PATH           | Python interpreter, as a path or a name on PATH |
| `--name` | `meshcore-meshpotato-bot`   | Service name |
| `--uninstall` |                             | Remove the service |

The installer:

1. Checks the script exists and `meshcore` imports.
2. Adds you to the `uucp` group if needed.
3. Sets `config.toml` to mode 600 if it holds the API key. Otherwise writes the key file from `METOFFICE_API_KEY` if that file is missing.
4. Picks a stable `/dev/serial/by-id/` port name if you don't give `--port`.
5. Writes `/etc/systemd/system/meshcore-meshpotato-bot.service`.
6. Disables ModemManager if it's running, because it grabs `ttyACM` radios.
7. Enables and starts the service.

The unit it writes:

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

---

## 7. Manage the service

| Task | Command |
|------|---------|
| Status | `sudo systemctl status meshcore-meshpotato-bot` |
| Live logs | `journalctl -u meshcore-meshpotato-bot -f` |
| Logs since boot | `journalctl -u meshcore-meshpotato-bot -b` |
| Restart after changing `config.toml` or updating | `sudo systemctl restart meshcore-meshpotato-bot` |
| Stop | `sudo systemctl stop meshcore-meshpotato-bot` |
| Start | `sudo systemctl start meshcore-meshpotato-bot` |
| Turn off at boot | `sudo systemctl disable meshcore-meshpotato-bot` |
| Remove | `./install_mesh_potato_bot_service.sh --uninstall` |

Stop the service before running the bot by hand, or the two fight over the serial port.

The Pi needs the correct time for the schedule. Check with `timedatectl`. If NTP is off:

```bash
sudo timedatectl set-ntp true
```

---


## Credits

- Built on the `https://github.com/meshcore-dev/meshcore_py` examples `serial_pingbot.py` and `serial_rss_bot.py`
- Weather: Met Office Weather DataHub, Site Specific API
- Geocoding: postcodes.io
