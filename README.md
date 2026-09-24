# MeshPotato 🥔: MeshCore WX, Ping, Bot

A MeshCore chat bot for a Raspberry Pi running Arch Linux ARM. It answers ping and test commands, posts Met Office weather, rate limits spam and sends scheduled messages. It runs as a systemd service and starts on boot.

Files in this repo:

| File | Purpose |
|------|---------|
| `run_bot.py` | The bot, with emoji weather replies |
| `install_mesh_potato_bot_service.sh` | Installs the bot as a systemd service |
| `README.md` | This guide |
| `.gitignore` | Keeps your API key and Python caches out of git |

---

## Requirements

- Raspberry Pi with Arch Linux ARM (the default user is `alarm`)
- A MeshCore companion radio on USB serial firmware
- Python 3.10 or newer
- A free Met Office DataHub API key
- Internet access for the Pi

---

## 1. What the bot does

### Commands

Send these in a listening channel or as a direct message.

Weather and ping commands work with or without a leading `!`. Help and the fun commands need the `!`. The bot ignores `help` or `roll` sent without it, so normal chat doesn't trigger them.

#### Weather and ping (`!` optional)

| Command | Reply |
|---------|-------|
| `ping` | `@[You] Pong (2 hops, a1:b2)` with hop count and path |
| `test` | `@[You] Test OK (2 hops, a1:b2)` |
| `wx` | Current weather for the default location |
| `wx <location>` | Current weather for a location |
| `wxh` / `wxh <location>` | Hour-by-hour outlook for the next few hours |
| `wxf` / `wxf <location>` | 3-day forecast |

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

`!eightball` needs a question. It picks from 19 answers: 8 yes, 5 unsure and 6 no. Edit `EIGHTBALL_ANSWERS` in the script to change them.

Dice, coin and eight ball use Python's `SystemRandom`, which draws on the operating system's random source.

The fun commands count toward the same rate limits as the weather commands.

`<location>` accepts:

- A name from `LOCATIONS` in the script: `wx cambridge` (not case sensitive)
- A UK postcode: `wx NR1 3JU`
- The first half of a postcode: `wx NR1`
- A UK town or village: `wx Cromer`
- Latitude and longitude: `wx 52.63,1.30`

Postcodes and place names are looked up on postcodes.io, which is free and needs no key.

### Example replies

```
🌙 Norwich 23:00 Clear 🌡️14°C (feels 13°) 💨NW 6mph gust 15 ☔0% 💧humidity 73%
🕒 Norwich: 23h ☁️13° ☔10% | 00h ☁️13° ☔20% | 01h 🌧️12° ☔30% | 02h 🌧️11° ☔40%
📅 Norwich: Wed ☁️ 17/8° ☔10% | Thu 🌧️ 18/8° ☔60% | Fri ☀️ 19/8° ☔5%
```

| Symbol | Meaning |
|--------|---------|
| 🌡️ 14°C | Air temperature |
| feels 13° | Feels-like temperature, after wind chill and humidity |
| 💨 NW 6mph | Wind direction (where it blows from) and average speed |
| gust 15 | Peak gust speed, same units |
| ☔ 0% | Chance of rain |
| 💧 humidity 73% | Relative humidity |
| 17/8° | Day high / night low |
| 23h | Hour of the day, 24-hour clock (23h = 23:00) |

Set `USE_EMOJI = False` for plain text:

```
Norwich 23:00 | Clear | 14C feels 13C | Wind NW 6mph gust 15 | Rain 0% | Humidity 73%
Norwich: 23h Partly cloudy 13C 10% | 00h Cloudy 13C 20% | 01h Light rain 12C 30%
```

### Hourly outlook (wxh)

`wxh` lists the coming hours with weather, temperature and chance of rain. It adds hours until it reaches `WXH_HOURS` or runs out of message space. With emojis and a short sender name, about 4 hours fit in one message.

To cover a longer period in one message, space the hours out with `WXH_STEP_HOURS`:

| `WXH_STEP_HOURS` | 4 entries cover |
|------------------|-----------------|
| `1` | The next 4 hours |
| `2` | The next 8 hours |
| `3` | The next 12 hours |

`wxh` uses the same Met Office hourly data as `wx`, so a `wx` and a `wxh` for the same place within 15 minutes cost one API call.

### Rate limiting

Every command is checked against three limits. A command only runs and counts when all three allow it.

| Limit | Default | Key |
|-------|---------|-----|
| Per user | 3 per 60s | Sender name in channels, public key in DMs |
| Per channel | 8 per 60s | Channel index, or one shared bucket for DMs |
| Global | 20 per 60s | Whole bot |

A user who hits their limit gets one notice per window: `Slow down, try again in 42s`. Other limits drop the command without a reply. Radio sends are queued with at least 3 seconds between them. Public keys in `ADMIN_PUBKEYS` skip all limits.

Channel messages carry only the sender's display name, not their key. Someone who changes their name gets a fresh per-user allowance. The per-channel and global limits still apply.

### Scheduled messages

The bot sends messages at set times from the `SCHEDULED_MESSAGES` list. Scheduled messages skip the rate limits but still go through the send queue.

---

## 2. Get a Met Office API key

1. Register at https://datahub.metoffice.gov.uk/
2. Subscribe to the **Site Specific** (Global Spot) free plan.
3. Copy the API key. It is about 1670 characters long, so copy all of it.

The free plan allows 360 calls a day. The bot caches each forecast for 15 minutes to stay inside that.

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
cd ~/mesh-potato
git pull
sudo systemctl restart meshcore-meshpotato-bot
```

### 3.5 Store the API key

The service doesn't read `~/.bashrc`, so keep the key in a file:

```bash
mkdir -p ~/.config/meshcore
nano ~/.config/meshcore/metoffice_key     # paste the key, save
chmod 600 ~/.config/meshcore/metoffice_key
```

The bot looks for the key in this order:

1. `METOFFICE_API_KEY` environment variable
2. The file named in `METOFFICE_KEY_FILE`
3. `~/.config/meshcore/metoffice_key`
4. `metoffice_key.txt` in the same folder as the script

If `METOFFICE_API_KEY` is already set in your shell when you run the installer, it writes the key file for you.

---

## 4. Configure the bot

Edit the Config section at the top of `run_bot.py`.

| Setting | Example | Notes |
|---------|---------|-------|
| `SERIAL_PORT` | `"/dev/ttyACM0"` | Overridden by `--port` |
| `CHANNEL_IDXS` | `[1, 3]` | Channel slots the bot listens and replies on |
| `ANSWER_DMS` | `True` | Reply to direct messages |
| `TIMEZONE` | `ZoneInfo("Europe/London")` | Used for the schedule and reply times |
| `DEFAULT_LOCATION` | `"Norwich"` | Used by `wx` with no location |
| `LOCATIONS` | `{"Norwich": (52.6278, 1.2983)}` | Named shortcuts |
| `USE_EMOJI` | `True` | `False` for plain text |
| `USE_MPH` | `True` | `False` for m/s |
| `ROLL_MAX_DICE` | `10` | Most dice per `!roll` |
| `ROLL_MAX_SIDES` | `1000` | Most sides per die |
| `EIGHTBALL_ANSWERS` | 19 answers | List of `!eightball` replies |
| `WXH_HOURS` | `6` | Most hours `wxh` shows, if they fit |
| `WXH_STEP_HOURS` | `1` | Gap between `wxh` entries in hours |
| `WXH_MENTION_RESERVE` | `25` | Bytes kept free for other text when `{wxh}` is used in a scheduled message |
| `MAX_REPLY_BYTES` | `135` | MeshCore limits messages by bytes. Emojis take 4 to 7 bytes each |
| `WX_CACHE_SEC` | `900` | How long to reuse a forecast |
| `RATE_LIMIT_PER_USER` | `(3, 60)` | (max commands, seconds) |
| `RATE_LIMIT_PER_CHANNEL` | `(8, 60)` | |
| `RATE_LIMIT_GLOBAL` | `(20, 60)` | |
| `RATE_LIMIT_NOTIFY` | `True` | Send one "slow down" notice |
| `MIN_TX_GAP_SEC` | `3.0` | Gap between radio sends |
| `ADMIN_PUBKEYS` | `{"a1b2c3d4e5f6"}` | 12-hex-character key prefixes that skip limits |

### Scheduled messages

Each entry needs `text`, a target and one timing rule.

Target:

- `"channel": 1` sends to a channel slot
- `"dm": "a1b2c3d4e5f6"` sends to a contact's public key prefix

Timing:

| Rule | Example | Fires |
|------|---------|-------|
| `"time"` | `"07:30"` | Every day at 07:30 |
| `"time"` + `"days"` | `"07:30"`, `["mon","fri"]` | Those days only |
| `"at"` | `"2026-12-25 09:00"` | Once |
| `"every_minutes"` | `360`, `"start": "00:00"` | 00:00, 06:00, 12:00, 18:00 |

Tokens filled in at send time:

| Token | Becomes |
|-------|---------|
| `{time}` | `07:30` |
| `{date}` | `Wed 23 Sep` |
| `{wx}` | Current weather, default location |
| `{wx:Cambridge}` | Current weather for Cambridge |
| `{wxh}` / `{wxh:Ipswich}` | Hourly outlook |
| `{wxf}` / `{wxf:Ipswich}` | 3-day forecast |

Example:

```python
SCHEDULED_MESSAGES = [
    {"name": "morning-wx", "time": "07:30", "days": ["mon", "tue", "wed", "thu", "fri"],
     "channel": 1, "text": "Morning WX {wx}"},
    {"name": "commute", "time": "07:00", "days": ["mon", "tue", "wed", "thu", "fri"],
     "channel": 1, "text": "{wxh}"},
    {"name": "weekend-fcst", "time": "08:30", "days": ["sat", "sun"],
     "channel": 1, "text": "{wxf}"},
    {"name": "beacon", "every_minutes": 360, "start": "00:00",
     "channel": 3, "text": "WX bot online {time}. Send !help for commands."},
    {"name": "xmas", "at": "2026-12-25 09:00",
     "channel": 1, "text": "Merry Christmas."},
]
```

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

Run the full bot in the foreground (Ctrl+C to stop):

```bash
python run_bot.py --port /dev/ttyACM0
```

Look for these log lines:

```
Met Office API key loaded (1670 chars)
Connected on /dev/ttyACM0
Scheduler running with 3 entries
Listening on channels [1, 3] and DMs
```

Send `ping`, `!roll 2d6` and `!help` from another node on channel 1 and check for the replies.

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
| `--python` | `python3` on PATH           | Python interpreter |
| `--name` | `meshcore-meshpotato-bot`   | Service name |
| `--uninstall` |                             | Remove the service |

The installer:

1. Checks the script exists and `meshcore` imports.
2. Adds you to the `uucp` group if needed.
3. Writes the key file from `METOFFICE_API_KEY` if the file is missing.
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
WorkingDirectory=/home/alarm/mesh-potato
Environment=HOME=/home/alarm
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /home/alarm/mesh-potato/run_bot.py --port /dev/serial/by-id/usb-...
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
| Restart after editing the script | `sudo systemctl restart meshcore-meshpotato-bot` |
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
