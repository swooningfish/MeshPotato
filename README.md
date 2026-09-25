# MeshPotato 🥔: MeshCore WX, Ping, Bot

MeshPotato is a chat bot for a [MeshCore](https://github.com/meshcore-dev) mesh radio network. It runs on a Raspberry Pi with a MeshCore companion radio plugged in over USB. It listens on your chosen channels and in direct messages, and replies to commands such as `ping`, `!wx` or `!help`.

What it can do:

- 🏓 **Signal checks:** hops, SNR, RSSI and the repeaters your message went through
- 👥 **Who's about:** who the bot has heard, where they are, and which frequencies to try (works without internet)
- 📮 **Mailbox:** leave a message for someone out of range, delivered when the bot next hears them
- 🌦️ **Weather:** Met Office current weather, hourly and 3-day forecasts, and weather warnings
- 🌅 **Sky and air:** sunrise, sunset, moon phase, aurora alerts, air quality and pollen
- 📻 **Radio conditions:** HF bands, VHF E-skip and UHF tropo
- 🧮 **Handy tools:** unit conversion, Ohm's law, resistor colour codes
- 🎲 **Fun:** dice, coin flip and a magic eight ball
- ⏰ **Scheduled posts:** a morning forecast, a beacon, anything you like at set times
- 🛡️ **Admin tools and rate limiting** to keep things tidy

It runs as a systemd service, so it starts on boot and restarts if it crashes.

---

## Contents

- [Using the bot](#using-the-bot)
- [Setup](#setup)
- [Managing the service](#managing-the-service)
- [Troubleshooting](#troubleshooting)
- [More documentation](#more-documentation)
- [Credits](#credits) · [License](#license)

---

## Using the bot

Send commands on a channel the bot listens on, or as a direct message (DM) to the bot.

- `ping` and `test` must be the **whole message** (the `!` is optional).
- Every other command **starts with `!`**, so normal chat never triggers the bot.
- Send `!help` on the mesh to see the help topics.

| Command | What you get |
|---------|--------------|
| `ping` | `🏓 Pong (2 hops)` |
| `test` | Hops, SNR, RSSI and your distance from the bot |
| `!path` | The repeaters your message came through |
| `!dist` | The distance of each leg of that path |
| `!who [hours or rpt]` | People (or repeaters) the bot has heard lately |
| `!status <name>` | When the bot last heard someone, and where they are |
| `!bearing <name or place>` | Distance and compass direction to a contact or place |
| `!freq [topic]` | Useful frequencies: PMR446, CB, ham, marine, air band, this mesh |
| `!mail <name> <message>` | Leave a message for someone (DM to the bot only) |
| `!clearmail [name]` | Cancel your waiting messages (DM to the bot only) |
| `!wx [place]` | Current weather |
| `!wxh [place]` | Hour-by-hour outlook |
| `!wxf [place]` | 3-day forecast |
| `!warn [place]` | Met Office weather warnings, then alerts if they change |
| `!sun [place]` | Sunrise, sunset and daylight |
| `!moon` | Moon phase |
| `!aurora` | Aurora alert level |
| `!aq [place]` / `!pollen [place]` | Air quality / pollen forecast |
| `!hf` / `!vhf` / `!uhf [place]` | Radio conditions |
| `!conv 10 mi km` | Unit conversion |
| `!ohm 12v 2a` | Ohm's law and power |
| `!res 4k7` | Resistor colour code (or give the colours) |
| `!roll 2d6` / `!flipacoin` / `!eightball <question>` | Fun |

`[place]` is optional. Leave it out to use the bot's home location, or give a town (`Cromer`), a UK postcode (`NR1 3JU` or `NR1`), a saved place name, or `lat,lon` (`52.63,1.30`).

Admins get extra commands (mute, post as the bot, stats) by DM. Example replies, aliases and the meaning of every emoji are in the [command guide](docs/commands.md).

---

## Setup

You need:

- A Raspberry Pi (or other Linux computer) with internet access
- A MeshCore companion radio with **USB serial** firmware, plugged into the Pi
- Python 3.11 or newer
- Optional: a free Met Office API key, for `!wx`, `!wxh` and `!wxf`. Everything else works without it.

These steps are for Arch Linux ARM, where the default user is `alarm`. On Raspberry Pi OS or Debian, use `sudo apt install python3 python3-pip git` instead of `pacman`, and the `dialout` group instead of `uucp`.

Run everything as your normal user, not root.

### 1. Install the software

```bash
sudo pacman -S --needed python python-pip tzdata git
pip install --user --break-system-packages meshcore
```

`meshcore` is the only extra Python package the bot needs.

<details>
<summary>Prefer a virtual environment?</summary>

```bash
python -m venv ~/meshbot
~/meshbot/bin/pip install meshcore
```

Then use `~/meshbot/bin/python` instead of `python`, and add `--python ~/meshbot/bin/python` when you install the service in step 7.

</details>

### 2. Allow access to the radio

```bash
sudo usermod -aG uucp alarm
```

**Log out and back in**, then check that `groups` lists `uucp`. Don't use `sudo` to run the bot instead.

### 3. Download the bot

```bash
cd ~
git clone https://github.com/swooningfish/MeshPotato.git
cd MeshPotato
```

Leave it in `~/MeshPotato`, and run the rest of the commands from there.

### 4. Get a Met Office API key (optional)

1. Register at https://datahub.metoffice.gov.uk/
2. Subscribe to the free **Site Specific** (Global Spot) plan.
3. Copy the key. It's about 1670 characters long, so make sure you get all of it.

The free plan allows 360 calls a day. The bot caches forecasts and stops at 300 calls a day, so you won't go over.

### 5. Configure the bot

```bash
cp config.example.toml config.toml
nano config.toml
```

Every line in the file starts with `#`, which means "use the default". To change a setting, remove the `#` and edit the value. Most people only need these:

```toml
metoffice_api_key = "paste-your-key-here"   # whole key on one line
channel_idxs = [1, 3]                       # channel slots the bot uses
default_location = "Norwich"                # home location for weather etc.
admin_pubkeys = ["a1b2c3d4e5f6"]            # first 12 characters of your public key
```

If you added the API key, keep the file private:

```bash
chmod 600 config.toml
```

**Not sure of your channel numbers?** Run `python channel_list.py`. It lists each channel on the radio with its slot number:

```
Channels:
    0  Public
    1  Norfolk
    3  Test
```

By default the bot also posts a morning weather message and a weekend forecast to channel 1. See [Scheduled messages](docs/settings.md#scheduled-messages) to change or turn these off. Every setting is listed in the [settings guide](docs/settings.md).

### 6. Test it

Check the internet side first. This prints a reply and exits:

```bash
python run_bot.py --wx
```

Then run the full bot (Ctrl+C to stop):

```bash
python run_bot.py
```

You should see `Connected on /dev/ttyACM0` and `Listening on channels [1, 3] and DMs`. From another node, send `ping` on one of the bot's channels. You should get `🏓 Pong` back.

**Stop the bot with Ctrl+C before the next step.** Only one program can use the radio at a time.

### 7. Install it as a service

```bash
chmod +x install_mesh_potato_bot_service.sh
./install_mesh_potato_bot_service.sh
```

The script asks for your password when it needs sudo. It finds the radio, sets up the service, and starts it. The bot now runs in the background and starts on every boot.

Installer options (`--port`, `--python`, `--uninstall` and more) are in the [settings guide](docs/settings.md#service-installer).

---

## Managing the service

| To | Run |
|----|-----|
| Check it's running | `sudo systemctl status meshcore-meshpotato-bot` |
| Watch the logs | `journalctl -u meshcore-meshpotato-bot -f` |
| Restart (after editing `config.toml`) | `sudo systemctl restart meshcore-meshpotato-bot` |
| Stop / start | `sudo systemctl stop meshcore-meshpotato-bot` / `start` |
| Uninstall | `./install_mesh_potato_bot_service.sh --uninstall` |

To update to the latest version (your `config.toml` is kept):

```bash
cd ~/MeshPotato
git pull
sudo systemctl restart meshcore-meshpotato-bot
```

Stop the service before running the bot or `channel_list.py` by hand, or they will fight over the radio.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Permission denied` on `/dev/ttyACM0` | Add yourself to the `uucp` group (step 2), then log out and back in |
| `bad interpreter` or `$'\r': command not found` | The files have Windows line endings. Run `sed -i 's/\r$//' *.sh *.py` |
| The bot can't connect, or the port is busy | Something else has the radio open. Stop the service or the other copy of the bot |
| The bot doesn't answer anything | Check `channel_idxs` matches your channel (`python channel_list.py`), and that the bot isn't muted |
| The bot doesn't answer `!wx` | No API key was found. The startup log says where it looked |
| `!wx somewhere` gets no reply | The place wasn't found. Try a postcode (always use one for Northern Ireland) |
| The bot stops at startup naming a setting | That setting has the wrong type in `config.toml`, such as `"yes"` instead of `true` |
| Scheduled messages at the wrong time | Check the Pi's clock with `timedatectl`. Turn on time sync with `sudo timedatectl set-ntp true` |
| `Slow down, try again in 42s` | You sent too many commands. Wait and try again |

To see every message the bot hears, set `log_level = "DEBUG"` in `config.toml`, restart, and watch the logs.

---

## More documentation

- [Command guide](docs/commands.md): every command in detail, with example replies, aliases and what the emojis mean
- [How it works](docs/howitworks.md): what happens behind the scenes, one area at a time (mail delivery, the heard list, caching, rate limits and more)
- [Settings guide](docs/settings.md): every setting, scheduled messages, API key options and the service installer

### Files

| File | Purpose |
|------|---------|
| `run_bot.py` | Starts the bot |
| `meshpotato/` | The bot's code, one module per feature (`wx.py`, `mail.py`, `commands.py` and so on) |
| `config.example.toml` | Example settings. Copy it to `config.toml` |
| `channel_list.py` | Lists the channels on your radio |
| `install_mesh_potato_bot_service.sh` | Installs the bot as a service |
| `tests/` | Automated tests: `python -m pytest tests` (no radio needed) |

The bot creates `heard.json`, `mail.json` and `authed_mail_users.json` as it runs. These, and `config.toml`, are kept out of git.

### To do

- `!tide <place>`: UK tide times from the [ADMIRALTY UK Tidal API](https://www.admiralty.co.uk/access-data/apis)

---

## Credits

- Built on the [meshcore_py](https://github.com/meshcore-dev/meshcore_py) examples `serial_pingbot.py` and `serial_rss_bot.py`
- Weather and warnings: Met Office Weather DataHub and warnings RSS feed
- Place lookup: postcodes.io
- Aurora alerts: [AuroraWatch UK](https://aurorawatch.lancs.ac.uk/), Lancaster University
- Air quality, pollen and UHF tropo data: [Open-Meteo](https://open-meteo.com/) (CC BY 4.0), using Copernicus CAMS data
- HF and VHF conditions: Paul, N0NBH, [hamqsl.com](https://www.hamqsl.com/solar.html)

## License

MIT, see [LICENSE](LICENSE).
