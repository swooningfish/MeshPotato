# How it works

What MeshPotato does behind the scenes, one area at a time. For what each command replies, see the [command guide](commands.md). Setting names in `code` can be changed in `config.toml` (see the [settings guide](settings.md)).

## Contents

- [Messages and replies](#messages-and-replies)
- [Signal reports (test)](#signal-reports-test)
- [Paths and repeater names](#paths-and-repeater-names)
- [Positions and distances](#positions-and-distances)
- [The heard list (!who, !status)](#the-heard-list-who-status)
- [Mailbox delivery (!mail)](#mailbox-delivery-mail)
- [Place lookup](#place-lookup)
- [Weather and the Met Office quota](#weather-and-the-met-office-quota)
- [Weather warning alerts](#weather-warning-alerts)
- [Sun and moon](#sun-and-moon)
- [Data sources and caching](#data-sources-and-caching)
- [UHF tropo forecast](#uhf-tropo-forecast)
- [Admins and trust](#admins-and-trust)
- [Mute](#mute)
- [Rate limiting](#rate-limiting)
- [Saving to disk](#saving-to-disk)
- [Clock and time](#clock-and-time)

---

## Messages and replies

- The bot listens on the channel slots in `channel_idxs` and, if `answer_dms` is on, in direct messages.
- MeshCore limits messages by bytes, not characters. Replies are kept under `max_reply_bytes` (135). Emojis take 4 to 7 bytes each, so when a reply is too long the bot drops the least important parts first.
- Replies go into a send queue, with at least 3 seconds between radio sends (`min_tx_gap_sec`).

## Signal reports (test)

- The hop count comes from the message itself.
- The path itself isn't shown in `test`, because a long path would make the reply too long. Use `!path` for that.

## Paths and repeater names

- Each repeater in a path is the short hash of its public key that MeshCore puts in the packet.
- The bot adds a name when **exactly one** repeater in its contact list has a key starting with that hash. If none or several match, it shows the hex, because it can't be sure which one it was. Companions are skipped because they don't repeat.
- Names are cut to 12 characters.
- The contact list is read at startup and re-read every 5 minutes if the radio says it changed.
- A message sent by direct route doesn't carry its path, so the bot says so.
- `!path` shows the route the message already took. It doesn't send a MeshCore trace packet.
- For `!route`, the bot reads each channel's name from the radio at start-up. After a `!route` on a channel, it looks up the sender's `!route` message on meshrank.net every 2 seconds, then asks MeshRank to make a share link for it.

## Positions and distances

Distances in `test`, `!dist`, `!status` and `!bearing` are straight lines between advertised positions, not the path the radio waves took.

| Point | Where its position comes from |
|-------|-------------------------------|
| You | The location in your advert. In a DM the bot matches your key. In a channel it matches your name, only if exactly one contact has that name |
| A repeater | The location in its advert, when exactly one repeater matches the hash (as for path names) |
| The bot | The location set on the bot's radio, or `default_location` if it's one of your `[locations]` |

- A node only has a position if its owner set one and includes it in adverts. Many don't.
- MeshCore sends 0,0 for "no position", which the bot treats as unknown.
- `!bearing` gives the true (not magnetic) direction along the shortest path over the Earth.

## The heard list (!who, !status)

The bot keeps a list of nodes it has heard, stored by **public key**, from:

- **Adverts** from contacts. Adverts are signed with the node's key, so the bot knows who sent them.
- **DMs** from contacts. A DM is encrypted to the sender's key, which proves who it's from.

Channel messages **aren't** counted. They only carry a display name, which anyone can set, so they don't prove who is about.

- Each node is one entry, under its latest name. Renaming keeps the same entry, because the key doesn't change. Two radios with the same name stay separate, and `!status` shows the start of each key.
- `!status` matches the whole name first, then the start of a name, then any part of it, then the start of a key.
- If the bot hasn't heard someone itself, `!status` falls back to the last advert time in the contact list. That time comes from the other node's clock, so treat it as rough.
- Nodes not heard for 7 days (`heard_keep_days`) are dropped.

## Mailbox delivery (!mail)

- The bot holds the message and sends it by DM the next time it hears the recipient's **key**: an advert or a DM from them. Then it's removed from the mailbox.
- Channel messages **don't** trigger delivery. Otherwise someone could use the recipient's name to have their mail sent while they're out of range.
- **Sending needs a DM** because only a DM proves who the sender is. Only approved users (added by an admin with `!addmailuser`) and admins can send.
- **Receiving** needs no approval, but the recipient must be a companion in the bot's contacts, because the bot needs their key to DM them.
- **Delivery isn't confirmed.** The bot hands the DM to the radio and counts it as sent. If the recipient was only just in range, it can still be lost.
- **Busy bot:** mail only uses spare room in the send queue, keeping 5 places free for replies. The rest waits until the recipient is next heard, so a full queue never loses mail.
- **Mute:** while muted, mail waits and goes out the next time the recipient is heard after the mute ends.
- **Cancelling:** `!clearmail` only touches your own messages, and still works if an admin has since removed you from the approved list.

| Limit | Default | Setting |
|-------|---------|---------|
| Waiting from one sender to one recipient | 10 | `mail_max_per_pair` |
| Waiting from one sender in total | 30 | `mail_max_per_sender` |
| Waiting in the whole mailbox | 200 | `mail_max_total` |
| Length of one message | 100 bytes | `mail_max_bytes` |
| Days before an undelivered message is dropped | 7 | `mail_keep_days` |

Waiting message text is stored in `mail.json` on the Pi.

## Place lookup

- Saved place names (`[locations]`) and `lat,lon` are used directly and need no internet.
- Postcodes, half postcodes and town names are looked up on postcodes.io. Town names cover Great Britain only.
- `!bearing` looks the name up in the bot's contacts first, then as a place.
- `!warn` finds the Met Office region by looking up the nearest postcode. A place with no postcode within 2 km (such as out at sea) gets no reply. A place that can't be matched to a region gets the whole UK list.

## Weather and the Met Office quota

- The free Met Office plan allows 360 calls a day.
- Each forecast is cached for 30 minutes (`wx_cache_sec`). `!wx` and `!wxh` share the same hourly data, so both for the same place cost one call.
- After 300 calls in a UTC day (`wx_daily_call_budget`), the bot stops calling. Cached forecasts are still served. New lookups get `WX: daily quota used, try tomorrow` until midnight UTC.
- The call count is kept in memory, so it restarts at 0 when the bot restarts.
- `!wxh` adds hours until it reaches `wxh_hours` or runs out of message space. `wxh_step_hours` spaces them out:

| `wxh_step_hours` | 4 entries cover |
|------------------|-----------------|
| `1` | The next 4 hours |
| `2` | The next 8 hours |
| `3` | The next 12 hours |

- **Without an API key**, `!wx`, `!wxh` and `!wxf` are ignored for everyone, left out of `!help`, and left blank in scheduled messages. A scheduled message that ends up empty is skipped.

## Weather warning alerts

- Warnings come from the free Met Office warnings RSS feed (no key, no quota). Each region's feed is cached for 10 minutes (`warn_cache_sec`).
- Warnings that have ended are left out. Red warnings are listed first.
- After someone sends `!warn`, the bot watches that region for 24 hours (`warn_watch_hours`) and posts to the same channel or DM when the warnings change.
- It posts when a warning is added, changes level or times, or is cancelled early. A warning simply running out isn't posted.
- It checks every 60 seconds (`warn_watch_tick_sec`) through the 10-minute cache, so a change posts up to about 11 minutes after the Met Office publishes it.
- Sending `!warn` again for the same region in the same place restarts the 24 hours.
- Up to 10 region and channel pairs are watched at once (`warn_watch_max`). Extra `!warn` requests still get a reply, but no watch.
- A scheduled `{warn}` message doesn't start a watch. Only a person sending `!warn` does.
- While muted, changes are held and posted when the mute ends. Watches are kept in memory, so restarting the bot ends them.

## Sun and moon

- The bot works these out itself, so they need no internet.
- Sunrise and sunset are for today in `timezone`, accurate to about a minute. They assume a flat horizon, so hills and buildings shift the real times a little.
- New, first quarter, full and last quarter are shown for about a day either side of the exact moment. Full and new moon dates are accurate to within an hour, so one falling near midnight can show the wrong day.

## Data sources and caching

Every source except the Met Office forecast is free and needs no key. The bot caches each one to be polite and to follow the providers' terms.

| Data | Source | Cached for | Setting |
|------|--------|------------|---------|
| Weather | Met Office DataHub | 30 minutes | `wx_cache_sec` |
| Warnings | Met Office RSS feed | 10 minutes | `warn_cache_sec` |
| Aurora | AuroraWatch UK | 5 minutes (never under 3) | `aurora_cache_sec` |
| Air quality and pollen | Open-Meteo | 1 hour | `air_cache_sec` |
| HF and VHF | N0NBH, hamqsl.com | 1 hour (never less) | `hf_cache_sec` |
| UHF tropo | Open-Meteo | 1 hour | `tropo_cache_sec` |
| Route links (`!route`) | MeshRank | Not cached | `meshrank_links` |

- **AuroraWatch UK** terms: non-commercial use, credit in every reply, and no more than one request every 3 minutes. The bot also sends a `Referer` header pointing at this repo, and uses their level names unchanged.
- **Open-Meteo** is free for non-commercial use, and its data is CC BY 4.0, so replies credit it. One request covers both `!aq` and `!pollen` for a place.
- **N0NBH** asks for credit and no more than one fetch an hour. `!hf` shows the day or night figures depending on whether the sun is up at `default_location`.

## UHF tropo forecast

UHF range, including MeshCore's 868 MHz, depends on the lower atmosphere, not the sun. Beyond line of sight the main effect is refraction, where signals bend back towards the ground. N0NBH has no UHF figures, so the bot works it out from the Open-Meteo weather forecast:

1. It takes temperature, humidity and pressure near the ground and at about 700 m up (the 925 hPa level).
2. It works out the radio refractivity at both heights, using the ITU-R P.453 formula.
3. It divides the difference by the height between them, giving a gradient in N-units per km.

A normal atmosphere is about -40 N/km. More negative means signals bend further and travel further.

| Gradient (N/km) | Level | Meaning |
|-----------------|-------|---------|
| above 0 | 🔽 Below normal | Shorter range than usual |
| 0 to -60 | ➖ Normal | Standard atmosphere |
| -60 to -79 | 🔼 Slightly enhanced | Some extra range |
| -79 to -157 | ⏫ Enhanced | Clearly longer paths |
| below -157 | 🚀 Ducting likely | Paths of hundreds of km possible |

- The -79 and -157 limits are from ITU-R. -60 is the bot's own early hint.
- The 700 m layer averages out thin inversions, so real ducting near the ground can be stronger than shown. Treat it as a guide.
- Ducting is most likely on calm nights and mornings under high pressure, and over the sea.
- Places above about 700 m get `no forecast data`.

## Admins and trust

- Admins are listed by public key in `admin_pubkeys`. The first 12 hex characters is enough, and case is ignored.
- Admin commands only work in DMs. A channel message carries only a display name, which anyone can set, so the bot can't tell an admin apart there.
- The same idea is used everywhere: only adverts and DMs prove who someone is, so only they count for the heard list, mail delivery, and permissions.

## Mute

- `!mute` is for quieting the bot during a net or event.
- While muted the bot ignores every command except admin DMs, with no reply and no "slow down" notices.
- Scheduled messages that fall due are skipped, not sent later.
- A new `!mute` replaces the old one. The longest mute is `mute_max_minutes` (24 hours).
- The mute is kept in memory, so restarting the bot ends it.
- `!say` can post to any channel slot, not only the ones the bot listens on. `📢 Queued for ch1` means it's in the send queue. If the radio then refuses it, the failure is only logged.

## Rate limiting

Every command is checked against three limits, and only runs if all three allow it:

| Limit | Default | Counted per |
|-------|---------|-------------|
| Per user | 3 per 60s | Name in channels, public key in DMs |
| Per channel | 8 per 60s | Channel (all DMs share one) |
| Global | 20 per 60s | The whole bot |

- A user who hits their limit gets one `Slow down, try again in 42s` notice per window. The other limits drop commands silently.
- Admins skip all limits in DMs, but not in channels, where the bot can't tell who they are.
- In channels, users are tracked by name. Changing name gives a fresh per-user allowance, but the channel and global limits still apply.
- Scheduled messages skip the rate limits but still go through the send queue.

## Saving to disk

To spare the Pi's SD card, the heard list (`heard.json`) and mailbox (`mail.json`) are kept in memory:

- They're read when the bot starts and written when it stops cleanly (`systemctl stop` or `restart`, Ctrl+C, or a normal reboot).
- A power cut or crash loses anything since the last save. An admin can send `!save` to write them straight away.
- The list of approved mail users (`authed_mail_users.json`) is written as soon as it changes.

## Clock and time

- The schedule, `!who` times and reply times use the Pi's clock and `timezone`.
- Without internet the Pi can't set its clock. If you rely on the bot offline, fit a real-time clock or GPS.
- Check the clock with `timedatectl`.
- Stats, the Met Office call count, warning watches and the mute all reset when the bot restarts.
