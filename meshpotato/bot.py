"""The bot: radio events in, replies out."""

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from meshcore import MeshCore, EventType

from . import config as cfg
from . import state
from . import heard
from . import mail
from . import alerts
from . import stats
from .state import mute_remaining
from .text import split_sender
from .contacts import CHAT_NODE_TYPE, contact_by_key, contact_name, is_admin, repeater_names
from .rx import latest_rx, message_rx_info, parse_rx_log_data
from .heard import heard_path
from .mail import format_mail, mail_path, mail_users_path
from .ratelimit import RateLimiter
from .net import TTLCache
from .tx import Sender
from .schedule import scheduler, validate_schedule
from .commands import command_allowed, COMMANDS, parse_command, run_command

_LOGGER = logging.getLogger("meshpotato_bot")


class Bot:
    """Wires the radio to the commands. Notes who is heard, passes on waiting !mail,
    applies the admin, mute and rate limit rules, and runs each command in the background."""

    def __init__(self, meshcore: MeshCore, sender: Optional[Sender] = None):
        self.mc = meshcore
        self.sender = sender or Sender(meshcore)
        self.limiter = RateLimiter()
        self.self_name = (meshcore.self_info or {}).get("name", "")
        self.running: set[asyncio.Task] = set()      # keeps command tasks alive until they finish
        self.subs: list = []

    # ---------- Contacts and who was heard ----------
    def contact_by_key(self, key: str) -> Optional[dict]:
        return contact_by_key(self.mc.contacts or {}, key)

    async def refresh_contacts(self) -> None:
        """Contacts give !path its repeater names. Only re-read when the radio says they changed."""
        try:
            await asyncio.wait_for(self.mc.ensure_contacts(follow=True), timeout=30)
        except Exception as ex:
            _LOGGER.warning("Contact list read failed, !path will show hashes only: %s", ex)

    async def read_channel_names(self) -> None:
        """MeshRank finds a !route message by its channel name, e.g. '#test'."""
        for idx in cfg.CHANNEL_IDXS:
            try:
                result = await asyncio.wait_for(self.mc.commands.get_channel(idx), timeout=10)
            except Exception as ex:
                _LOGGER.warning("Channel %s name read failed, !route won't work there: %s", idx, ex)
                continue
            if result.type == EventType.CHANNEL_INFO and (result.payload or {}).get("channel_name"):
                state.channel_names[idx] = result.payload["channel_name"]

    def heard(self, name: str, via: str, info: Optional[dict] = None, key: str = "",
              repeater: bool = False) -> None:
        """An advert (signed) or DM (encrypted to the key) proves this key is about: note it
        for !who and !status, then pass on any !mail waiting for it. Channel messages only
        carry a name, so they never come here."""
        heard.log.record(name, via, key, info, repeater=repeater)
        if repeater or mute_remaining() > 0:
            return
        # Take only what the send queue can hold, leaving room for replies. A message
        # taken but dropped by a full queue would be lost, so the rest wait for next time
        for m in mail.box.take_for(key, limit=self.sender.room() - cfg.MAIL_TX_RESERVE):
            contact = self.contact_by_key(m["to_key"])
            if contact is None:
                _LOGGER.warning("Mail for %s dropped, no longer in contacts", m["to"])
                continue
            _LOGGER.info("Mail from %s to %s passed on", m["from"], m["to"])
            self.sender.dm(m["to_key"], format_mail(m))

    # ---------- Radio events ----------
    async def on_advert(self, event) -> None:
        """An advert tells !who and !status a node is still about, repeaters included."""
        payload = event.payload or {}
        key = payload.get("public_key", "")
        contact = self.contact_by_key(key) or (payload if payload.get("adv_name") else None)
        if contact and contact_name(contact) != self.self_name:
            self.heard(contact_name(contact), "advert", key=key,
                       repeater=contact.get("type") != CHAT_NODE_TYPE)

    async def on_rx_log_data(self, event) -> None:
        parsed = parse_rx_log_data(event.payload or {})
        if parsed:
            latest_rx.clear()
            latest_rx.update(parsed, at=time.monotonic())

    async def on_channel_message(self, event) -> None:
        msg = event.payload or {}
        chan = msg.get("channel_idx")
        if chan not in cfg.CHANNEL_IDXS:
            return
        sender_name, body = split_sender(msg.get("text", ""))
        if self.self_name and sender_name == self.self_name:
            return
        stats.counters.heard += 1
        rx_info = message_rx_info(msg)
        cmd, arg = parse_command(body)
        _LOGGER.log(logging.INFO if cmd else logging.DEBUG, "RX ch%s %s: %s", chan, sender_name, body)
        self.dispatch(cmd, arg, sender_name,
                      user_key=f"name:{sender_name.lower()}",
                      chan_key=f"ch:{chan}",
                      reply=lambda t: self.sender.channel(chan, t),
                      target=("chan", chan),
                      rx_info=rx_info)

    async def on_contact_message(self, event) -> None:
        msg = event.payload or {}
        prefix = msg.get("pubkey_prefix", "")
        if not prefix:
            return
        text = msg.get("text", "")
        stats.counters.heard += 1
        rx_info = message_rx_info(msg)
        contact = self.contact_by_key(prefix)
        if contact:
            self.heard(contact_name(contact), "dm", rx_info, key=prefix)
        cmd, arg = parse_command(text)
        _LOGGER.log(logging.INFO if cmd else logging.DEBUG, "RX dm %s: %s", prefix, text)
        self.dispatch(cmd, arg, "",
                      user_key=prefix,
                      chan_key="dm",
                      reply=lambda t: self.sender.dm(prefix, t),
                      target=("dm", prefix),
                      rx_info=rx_info,
                      admin=is_admin(prefix))

    # ---------- Commands ----------
    def dispatch(self, cmd: str, arg: str, sender_name: str, user_key: str, chan_key: str,
                 reply: Callable[[str], None], target: tuple[str, Any], rx_info: dict[str, Any],
                 admin: bool = False) -> None:
        if not cmd:
            return
        if not command_allowed(cmd, admin):
            why = "no Met Office API key" if COMMANDS[cmd].needs_wx else "admin only"
            _LOGGER.info("Ignored %s from %s (%s)", cmd, user_key, why)
            return
        if not admin and mute_remaining() > 0:
            _LOGGER.info("Muted, ignoring %s from %s", cmd, user_key)
            return
        if not admin:
            ok, bucket, retry = self.limiter.check(user_key, chan_key)
            if not ok:
                stats.counters.limited += 1
                _LOGGER.info("Rate limited (%s) %s on %s, retry %ss", bucket, user_key, chan_key, retry)
                if cfg.RATE_LIMIT_NOTIFY and bucket == "user" and self.limiter.should_notify(user_key):
                    who = f"@[{sender_name}] " if sender_name else ""
                    reply(f"{who}Slow down, try again in {retry}s")
                return
        stats.counters.commands[cmd] += 1
        # Run in the background so a slow weather lookup doesn't hold up other messages
        task = asyncio.create_task(self._respond(cmd, arg, sender_name, rx_info, reply, target),
                                   name=f"cmd {cmd}")
        self.running.add(task)
        task.add_done_callback(self.running.discard)

    async def _respond(self, cmd, arg, sender_name, rx_info, reply, target) -> None:
        try:
            text = await run_command(cmd, arg, sender_name, rx_info, target)
        except Exception:
            _LOGGER.exception("Command %s failed", cmd)
            return
        if text:
            reply(text)

    async def drain(self) -> None:
        """Wait for the commands still running."""
        while self.running:
            await asyncio.gather(*self.running)

    # ---------- Running ----------
    async def housekeeping(self) -> None:
        while True:
            await asyncio.sleep(300)
            self.limiter.cleanup()
            for cache in list(TTLCache.all):
                cache.cleanup()
            heard.log.prune()      # in memory only: HEARD_FILE and MAIL_FILE are written on exit or by !save
            mail.box.prune()
            await self.refresh_contacts()

    def subscribe(self) -> None:
        self.subs = [
            self.mc.subscribe(EventType.CHANNEL_MSG_RECV, self.on_channel_message),
            self.mc.subscribe(EventType.RX_LOG_DATA, self.on_rx_log_data),
        ]
        if cfg.ANSWER_DMS:
            self.subs.append(self.mc.subscribe(EventType.CONTACT_MSG_RECV, self.on_contact_message))
        for name in ("ADVERTISEMENT", "NEW_CONTACT"):
            if hasattr(EventType, name):
                self.subs.append(self.mc.subscribe(getattr(EventType, name), self.on_advert))

    async def run(self) -> None:
        """Load what was saved, listen until stopped, then save and disconnect."""
        state.radio, state.tx = self.mc, self.sender         # for !stats (battery), !say and command contacts
        schedule = validate_schedule(cfg.SCHEDULED_MESSAGES)
        await self.mc.start_auto_message_fetching()
        await self.refresh_contacts()
        _LOGGER.info("%d repeaters known for !path names", len(repeater_names()))
        await self.read_channel_names()
        _LOGGER.info("Channel names: %s", state.channel_names)
        heard.log.load(heard_path())
        _LOGGER.info("%d nodes remembered for !who and !status", len(heard.log.nodes))
        mail.box.load(mail_path())
        mail.allowed_users.load(mail_users_path())
        _LOGGER.info("%d users allowed to use !mail", len(mail.allowed_users.users))
        _LOGGER.info("%d messages waiting in the !mail box", len(mail.box.messages))
        self.subscribe()

        tasks = [
            asyncio.create_task(self.sender.run(), name="sender"),
            asyncio.create_task(scheduler(self.sender, schedule), name="scheduler"),
            asyncio.create_task(self.housekeeping(), name="housekeeping"),
            asyncio.create_task(alerts.watcher.run(self.sender), name="warn-watch"),
        ]
        _LOGGER.info("Listening on channels %s%s", cfg.CHANNEL_IDXS, " and DMs" if cfg.ANSWER_DMS else "")
        try:
            await asyncio.gather(*tasks)
        finally:
            # Only written here and by an admin !save, to spare the SD card. systemd stops the bot
            # with SIGINT, so this runs on stop, restart and a clean reboot. A power cut loses
            # what was heard and any mail left since the last save.
            heard.log.save(heard_path())
            mail.box.save(mail_path())
            for t in tasks + list(self.running):
                t.cancel()
            for s in self.subs:
                self.mc.unsubscribe(s)
            await self.mc.stop_auto_message_fetching()
            await self.mc.disconnect()
            _LOGGER.info("Disconnected")


async def main(port: str) -> None:
    if not cfg.MET_OFFICE_API_KEY:
        _LOGGER.warning("Met Office API key not found (env METOFFICE_API_KEY, metoffice_api_key "
                        "in config.toml, ~/.config/meshcore/metoffice_key or metoffice_key.txt). "
                        "!wx/!wxh/!wxf are ignored and left out of !help, and {wx} tokens "
                        "in scheduled messages are left blank.")
    else:
        _LOGGER.info("Met Office API key loaded from %s (%d chars)",
                     cfg.MET_OFFICE_KEY_SOURCE, len(cfg.MET_OFFICE_API_KEY))

    meshcore = await MeshCore.create_serial(port, cfg.BAUDRATE, debug=False, auto_reconnect=True)
    if meshcore is None:
        raise SystemExit(f"Could not connect on {port}")
    _LOGGER.info("Connected on %s", port)
    await Bot(meshcore).run()
