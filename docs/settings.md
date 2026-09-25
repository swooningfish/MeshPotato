# Settings guide

All settings go in `config.toml`. For how the features behind these settings work, see [How it works](howitworks.md).

## Contents

- [The config file](#the-config-file)
- [Met Office API key](#met-office-api-key)
- [All settings](#all-settings)
- [Scheduled messages](#scheduled-messages)
- [Service installer](#service-installer)
- [Running the tests](#running-the-tests)

---

## The config file

```bash
cp config.example.toml config.toml
nano config.toml
```

- Every line in the example starts with `#`, which means "use the default". Remove the `#` and edit the value to change a setting.
- Don't edit `run_bot.py`. `config.toml` is ignored by git, so `git pull` never clashes with your changes.
- After changing it, restart the bot: `sudo systemctl restart meshcore-meshpotato-bot`.
- The bot reads `config.toml` from its own folder. To use another file, pass `--config /path/to/file.toml` or set `MESHPOTATO_CONFIG`.
- A misspelled setting is logged and ignored. A value of the wrong type (such as `"yes"` instead of `true`) stops the bot at startup with a message naming the setting.

## Met Office API key

The easiest place for the key is `config.toml`:

```toml
metoffice_api_key = "paste-your-key-here"
```

Then run `chmod 600 config.toml` to keep it private.

If you'd rather keep it separate, put it in a key file instead:

```bash
mkdir -p ~/.config/meshcore
nano ~/.config/meshcore/metoffice_key     # paste the key, save
chmod 600 ~/.config/meshcore/metoffice_key
```

The bot uses the first key it finds, in this order:

1. The `METOFFICE_API_KEY` environment variable
2. `metoffice_api_key` in `config.toml`
3. The file named in `METOFFICE_KEY_FILE`
4. `~/.config/meshcore/metoffice_key`
5. `metoffice_key.txt` next to `run_bot.py`

The startup log says which one it used. The service doesn't read `~/.bashrc`, so an environment variable set there won't reach it.

## All settings

Anything you leave out keeps its default.

### Basics

| Setting | Default | Notes |
|---------|---------|-------|
| `metoffice_api_key` | none | Met Office key |
| `serial_port` | `"/dev/ttyACM0"` | The radio's port. `--port` overrides it |
| `channel_idxs` | `[1, 3]` | Channel slots the bot listens and replies on. Find them with `python channel_list.py` |
| `answer_dms` | `true` | Reply to direct messages |
| `timezone` | `"Europe/London"` | Used for the schedule and times in replies |
| `log_level` | `"INFO"` | `"DEBUG"` also logs every message heard |
| `default_location` | `"Norwich"` | Used when no place is given |
| `[locations]` | Norwich, Cambridge, Ipswich… | Your own named places, such as `Norwich = [52.6278, 1.2983]`. Replaces the built-in list |

### Replies

| Setting | Default | Notes |
|---------|---------|-------|
| `use_emoji` | `true` | `false` for plain text |
| `use_mph` | `true` | `false` for m/s |
| `dist_miles` | `false` | `true` for distances in miles |
| `max_reply_bytes` | `135` | Longest reply. Emojis take 4 to 7 bytes each |

### Weather

| Setting | Default | Notes |
|---------|---------|-------|
| `wx_cache_sec` | `1800` | How long to reuse a forecast |
| `wx_daily_call_budget` | `300` | Most Met Office calls per UTC day |
| `wxh_hours` | `6` | Most hours `!wxh` shows, if they fit |
| `wxh_step_hours` | `1` | Hours between `!wxh` entries |
| `wxh_mention_reserve` | `25` | Bytes kept free for other text when `{wxh}` is in a scheduled message |
| `warn_cache_sec` | `600` | How long to reuse the warnings feed |
| `warn_watch_hours` | `24` | How long after a `!warn` the bot posts changes |
| `warn_watch_max` | `10` | Most regions and channels watched at once |
| `warn_watch_tick_sec` | `60` | How often watched regions are checked |

### Other data

| Setting | Default | Notes |
|---------|---------|-------|
| `aurora_cache_sec` | `300` | Never less than 180 |
| `air_cache_sec` | `3600` | Air quality and pollen |
| `pollen_levels` | `{grass = [30, 50, 150]}` | Counts where Moderate, High and Very high start, per pollen type |
| `hf_cache_sec` | `3600` | HF and VHF feed. Never less than 3600 |
| `tropo_cache_sec` | `3600` | UHF tropo |

### Fun

| Setting | Default | Notes |
|---------|---------|-------|
| `roll_max_dice` | `10` | Most dice per `!roll` |
| `roll_max_sides` | `1000` | Most sides per die |
| `eightball_answers` | 19 answers | Your own list of `!eightball` replies |

### Who's about, mail and frequencies

| Setting | Default | Notes |
|---------|---------|-------|
| `who_hours` | `24` | How far back `!who` looks by default |
| `heard_keep_days` | `7` | Forget nodes not heard for this long |
| `heard_file` | `"heard.json"` | Where the heard list is saved |
| `mail_max_per_pair` | `10` | Most waiting messages from one sender to one recipient |
| `mail_max_per_sender` | `30` | Most waiting messages from one sender in total |
| `mail_max_total` | `200` | Most waiting messages overall |
| `mail_keep_days` | `7` | Drop undelivered messages after this long |
| `mail_max_bytes` | `100` | Longest message. Keep it about 35 under `max_reply_bytes` |
| `mail_users_file` | `"authed_mail_users.json"` | Keys allowed to use `!mail` |
| `mail_file` | `"mail.json"` | Where the mailbox is saved |
| `[freq_lists]` | pmr, cb, ham, hf, marine, air | Your own `!freq` topics, such as `local = "GB3XX 145.7250 -600k"`. A topic with the same name replaces the built-in one, and `""` removes it |

File paths are relative to the folder `run_bot.py` is in.

### Rate limits and admin

| Setting | Default | Notes |
|---------|---------|-------|
| `rate_limit_per_user` | `[3, 60]` | [most commands, per this many seconds] |
| `rate_limit_per_channel` | `[8, 60]` | |
| `rate_limit_global` | `[20, 60]` | |
| `rate_limit_notify` | `true` | Send one "slow down" notice |
| `min_tx_gap_sec` | `3.0` | Seconds between radio sends |
| `admin_pubkeys` | `[]` | Admin public keys (first 12 hex characters) |
| `mute_max_minutes` | `1440` | Longest `!mute` |
| `scheduled_messages` | built-in list | See below |

## Scheduled messages

Out of the box the bot posts three messages to channel 1:

- `Morning WX {wx}` at 07:30 on weekdays
- the 3-day forecast `{wxf}` at 08:30 at weekends
- `Merry Christmas.` at 09:00 on 25 December

To change them, add one `[[scheduled_messages]]` block per message to `config.toml`. **Adding any block replaces the whole built-in list**, so include every message you want.

To turn them off completely, add `scheduled_messages = []` near the top of `config.toml`, before any `[section]`.

```toml
[[scheduled_messages]]
name = "morning-wx"
time = "07:30"
days = ["mon", "tue", "wed", "thu", "fri"]
channel = 1
text = "Morning WX {wx}"
```

Each message needs `text`, a target and a timing rule. `name` is optional and shows in the logs.

**Target** (one of):

- `channel = 1` posts to a channel slot
- `dm = "a1b2c3d4e5f6"` sends to a contact by public key

**Timing** (one of):

| Rule | Example | Sends |
|------|---------|-------|
| `time` | `time = "07:30"` | Every day at 07:30 |
| `time` + `days` | `time = "07:30"`, `days = ["mon", "fri"]` | On those days only |
| `at` | `at = "2026-12-25 09:00"` | Once |
| `at` without a year | `at = "12-25 09:00"` | Every year on that date |
| `every_minutes` | `every_minutes = 360`, `start = "00:00"` | 00:00, 06:00, 12:00, 18:00 (5 minutes or more) |

**Tokens** in `text` are filled in when the message is sent. Add `:place` for somewhere other than `default_location`:

| Token | Becomes |
|-------|---------|
| `{time}` | `07:30` |
| `{date}` | `Wed 23 Sep` |
| `{wx}` / `{wx:Cambridge}` | Current weather |
| `{wxh}` / `{wxh:Ipswich}` | Hourly outlook |
| `{wxf}` / `{wxf:Ipswich}` | 3-day forecast |
| `{warn}` / `{warn:Ipswich}` | Weather warnings |
| `{sun}` / `{sun:Ipswich}` | Sunrise, sunset and daylight |
| `{moon}` | Moon phase |
| `{aurora}` | Aurora alert level |
| `{aq}` / `{aq:Ipswich}` | Air quality |
| `{pollen}` / `{pollen:Ipswich}` | Pollen forecast |
| `{hf}` | HF band conditions |
| `{vhf}` | VHF conditions |
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

Good to know:

- A message can send up to 2 minutes late (`schedule_grace_sec`), for example after a restart. Anything missed by more is skipped, and no backlog is sent.
- If a place in a token can't be found, the message still sends, with `WX: 'place' not found` in its place.
- A message with a mistake is logged at startup and skipped. The others still run.
- Messages are skipped while the bot is muted.
- The Pi needs the correct time. Check with `timedatectl`.

## Service installer

Run from `~/MeshPotato` as your normal user (it asks for sudo when needed):

```bash
./install_mesh_potato_bot_service.sh
```

| Option | Default | Purpose |
|--------|---------|---------|
| `--port` | first `/dev/serial/by-id/*` | The radio's serial port |
| `--python` | `python3` | Python to use, such as `~/meshbot/bin/python` for a virtual environment |
| `--script` | `run_bot.py` | Which bot file to run |
| `--name` | `meshcore-meshpotato-bot` | Service name |
| `--uninstall` | | Remove the service |

For example:

```bash
./install_mesh_potato_bot_service.sh --port /dev/ttyACM0
```

What the installer does:

1. Checks the bot is there and `meshcore` is installed.
2. Adds you to the `uucp` group if needed.
3. Makes `config.toml` private if it holds the API key. Otherwise, if `METOFFICE_API_KEY` is set in your shell and there's no key file, it writes one.
4. Picks a stable `/dev/serial/by-id/` name for the radio if you don't give `--port`.
5. Writes `/etc/systemd/system/meshcore-meshpotato-bot.service`.
6. Turns off ModemManager if it's running, because it grabs USB radios.
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

## Running the tests

The tests don't need the radio, the `meshcore` library or an API key:

```bash
sudo pacman -S --needed python-pytest
python -m pytest tests
```

You can also test each data source without the radio. These print a reply and exit:

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
