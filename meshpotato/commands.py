"""Command handling: the registry, !help and every command's handler."""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from . import config as cfg
from . import state
from . import heard
from . import mail
from . import alerts
from .state import mute_remaining
from .text import duration, plural
from .storage import save_part
from .contacts import contact_by_key, contact_name, is_admin, key_id, repeater_names
from .rx import format_hops, format_path, format_rx_report
from .geo import bot_position, distance_text, format_dist, get_bearing, haversine_km, sender_position
from .heard import format_status, format_who, Heard, heard_path
from .mail import add_mail_user, clear_mail, leave_mail, list_mail_users, Mailbox, mail_path, remove_mail_user
from .tools import convert_units, eightball, flip_coin, format_freq, ohms_law, resistor, roll_dice
from .wx import wx_available
from .alerts import lookup_warnings
from .stats import battery_mv, format_stats, format_uptime
from .reports import REPORTS

_LOGGER = logging.getLogger("meshpotato_bot")


# ---------- Registry ----------
@dataclass
class Ctx:
    """What a command handler gets: the command, its argument, who sent it and where the reply goes."""
    cmd: str
    arg: str = ""
    sender_name: str = ""                           # "" in a DM
    rx_info: dict[str, Any] = field(default_factory=dict)
    target: Optional[tuple[str, Any]] = None        # ("chan", idx) or ("dm", pubkey prefix)
    contacts: dict = field(default_factory=dict)

    @property
    def dm_key(self) -> str:
        """The sender's pubkey prefix in a DM, "" in a channel."""
        return self.target[1] if self.target and self.target[0] == "dm" else ""

    @property
    def mention(self) -> str:
        return f"@[{self.sender_name}] " if self.sender_name else ""

    @property
    def budget(self) -> int:
        """Bytes left for the reply after the mention."""
        return cfg.MAX_REPLY_BYTES - len(self.mention.encode("utf-8"))

    def sender_position(self) -> Optional[tuple[float, float]]:
        return sender_position(self.contacts, name=self.sender_name, key_prefix=self.dm_key)


@dataclass
class Command:
    name: str                       # as parse_command returns it: "ping", "!wx"
    handler: Callable[[Ctx], Awaitable[Optional[str]]]
    aliases: tuple[str, ...] = ()   # other words for it, without the "!"
    admin: bool = False             # only answered in a DM from a key in ADMIN_PUBKEYS. Channel
                                    # messages carry no key, so never answered in a channel
    needs_wx: bool = False          # ignored, and left out of !help, without a Met Office API key
    dm_only: bool = False           # a DM is encrypted to the sender's key, so the bot knows who sent
                                    # it. A channel message only carries a name, which anyone can use
    icon: str = ""                  # shown in the dm_only refusal
    help: str = ""                  # the !help topic that lists it
    mention: bool = True            # start the reply with @[sender]

    @property
    def plain(self) -> bool:
        """Plain commands work with or without a leading "!", but only as the whole message."""
        return not self.name.startswith("!")


COMMANDS: dict[str, Command] = {}
COMMAND_WORDS: dict[str, str] = {}      # every name and alias, without "!", to its command: "8ball" -> "!eightball"


def command(*names: str, aliases: tuple[str, ...] = (), **options) -> Callable:
    """Register the decorated handler as one or more commands, e.g. @command("!sun", aliases=("sunrise",))."""
    def register(handler):
        for name in names:
            COMMANDS[name] = cmd = Command(name, handler, tuple(aliases), **options)
            for word in (name.lstrip("!"),) + cmd.aliases:
                if word in COMMAND_WORDS:
                    raise ValueError(f"{word!r} is already {COMMAND_WORDS[word]}")
                COMMAND_WORDS[word] = name
        return handler
    return register


def parse_command(body: str) -> tuple[str, str]:
    """Return (cmd, arg). Bang commands come back as "!roll" etc.
    Unknown commands, bang commands sent without "!", and plain commands
    with extra text ("ping me later") return ("", "")."""
    body = body.strip()
    body = re.sub(r"^@\[[^\]]*\]\s*", "", body)     # strip a leading @[mention]
    if not body:
        return "", ""
    parts = body.split(None, 1)
    raw = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    name = COMMAND_WORDS.get(raw.lstrip("!/"), "")
    if not name:
        return "", ""
    if COMMANDS[name].plain:
        return (name, "") if not arg else ("", "")
    if raw.startswith("!"):
        return name, arg
    return "", ""


def command_allowed(cmd: str, admin: bool) -> bool:
    c = COMMANDS.get(cmd)
    if c is None or (c.needs_wx and not wx_available()):
        return False
    return admin or not c.admin


HELP_TOPICS = ("test", "net", "wx", "radio", "fun", "conv")


def help_text(topic: str = "") -> str:
    """!help lists the topics, !help<topic> or !help <topic> the commands in one.
    The weather commands are left out when there is no Met Office API key."""
    topic = topic.strip().lower().lstrip("!")
    topic = topic[4:] if topic.startswith("help") else topic
    if topic == "test":
        return "Mesh: ping (hops), test (hops, distance), !path (repeaters), !dist (leg distances). ping and test need no !"
    if topic == "wx":
        wx = "!wx now, !wxh hourly, !wxf 3-day, " if wx_available() else ""
        return f"Weather: {wx}!warn warnings, !sun, !moon, !aq air, !pollen. Add a place: !sun Cromer, !aq NR1"
    if topic == "radio":
        return "Radio: !hf HF bands, !vhf 6m/4m/2m, !uhf [place] tropo, !aurora geomagnetic. See !helpconv for dBm and Ohm's law"
    if topic == "fun":
        return "Fun: !roll [2d6+1] dice, !flip coin, !8ball <question>"
    if topic == "conv":
        return "Conversions: !conv 10 mi km, !conv 5 w dbm. !ohm two of V/A/Ω/W, e.g. !ohm 12v 2a. !res 4k7 or !res yellow violet red"
    if topic == "net":
        return "Net: !who [hours|rpt], !status <name>, !bearing <name|place>, !mail <name> <msg> (in a DM), !freq [topic]"
    return "Help: !helptest (ping, path), !helpnet (who, mail), !helpwx (weather), !helpradio (bands), !helpfun, !helpconv"


# ---------- Admin commands ----------
def _mute_end_text(seconds: float) -> str:
    return f"{datetime.now(cfg.TIMEZONE) + timedelta(seconds=seconds):%H:%M}"


def mute(arg: str) -> str:
    """!mute <minutes> starts or replaces a mute, !mute 0 ends it, !mute alone shows the state."""
    on, off = ("🔇 ", "🔊 ") if cfg.USE_EMOJI else ("", "")
    arg = arg.strip()
    if not arg:
        left = mute_remaining()
        if not left:
            return f"{off}Not muted"
        return f"{on}Muted, {duration(left)} left (until {_mute_end_text(left)})"
    if not arg.isdigit() or int(arg) > cfg.MUTE_MAX_MINUTES:
        return f"Use !mute <minutes> (1-{cfg.MUTE_MAX_MINUTES}), !mute 0 or !unmute to end"
    minutes = int(arg)
    if minutes == 0:
        return unmute()
    state.muted_until = time.monotonic() + minutes * 60
    _LOGGER.info("Muted for %d minutes", minutes)
    return f"{on}Muted for {duration(minutes * 60)}, until {_mute_end_text(minutes * 60)}"


def unmute() -> str:
    was_muted = mute_remaining() > 0
    state.muted_until = 0.0
    if was_muted:
        _LOGGER.info("Unmuted")
    return ("🔊 " if cfg.USE_EMOJI else "") + ("Unmuted" if was_muted else "Not muted")


def say(arg: str) -> str:
    """!say <ch> <text> queues text for a channel slot. Works while muted."""
    parts = arg.split(None, 1)
    if len(parts) < 2 or not parts[0].isdigit():
        return "Use !say <ch> <text>, e.g. !say 1 Net starts 20:00"
    if state.tx is None:
        return "Radio not ready"
    ch = int(parts[0])
    state.tx.channel(ch, parts[1])
    _LOGGER.info("Admin !say queued for ch%s", ch)
    return ("📢 " if cfg.USE_EMOJI else "") + f"Queued for ch{ch}"


def save_state(nodes: Optional[Heard] = None, mailbox: Optional[Mailbox] = None,
               heard_file: Optional[str] = None, mail_file: Optional[str] = None) -> str:
    """!save (admin): write the heard list and the mailbox now, before a power cut,
    rather than waiting for the bot to stop. '💾 12 nodes saved, 3 messages unchanged'."""
    nodes = heard.log if nodes is None else nodes
    mailbox = mail.box if mailbox is None else mailbox
    parts = [save_part(nodes, heard_file or heard_path(), plural(len(nodes.nodes), "node")),
             save_part(mailbox, mail_file or mail_path(), plural(len(mailbox.messages), "message"))]
    text = ("💾 " if cfg.USE_EMOJI else "") + ", ".join(parts)
    return text + (", see the log" if "failed" in text else "")


# ---------- The commands ----------
@command("ping", help="test")
async def _ping(ctx: Ctx) -> str:
    return f"{'🏓 ' if cfg.USE_EMOJI else ''}Pong {format_hops(ctx.rx_info)}"


@command("test", help="test")
async def _test(ctx: Ctx) -> str:
    # '@[Alice] 📡 RX in Norwich | 🐸 (2 hops) | 📏 34km'
    dish, ruler = ("📡 ", "📏 ") if cfg.USE_EMOJI else ("", "")
    parts = [f"{dish}RX in {cfg.DEFAULT_LOCATION.strip()}" if cfg.DEFAULT_LOCATION.strip() else "Test OK",
             format_rx_report(ctx.rx_info)]
    start, end = ctx.sender_position(), bot_position()
    if start and end:
        parts.append(ruler + distance_text(haversine_km(start, end)))
    return " | ".join(parts)


@command("!path", aliases=("trace",), help="test")
async def _path(ctx: Ctx) -> str:
    return format_path(ctx.rx_info, repeater_names(), budget=ctx.budget)


@command("!dist", help="test")
async def _dist(ctx: Ctx) -> str:
    return format_dist(ctx.rx_info, ctx.contacts, ctx.sender_position(), bot_position(), budget=ctx.budget)


@command("!bearing", aliases=("brg", "find"), help="net")
async def _bearing(ctx: Ctx) -> str:
    return await get_bearing(ctx.arg, ctx.contacts, ctx.sender_position())


@command("!status", aliases=("seen", "lastheard"), help="net")
async def _status(ctx: Ctx) -> str:
    return format_status(ctx.arg, heard.log, ctx.contacts, bot_position())


@command("!who", aliases=("heard",), help="net")
async def _who(ctx: Ctx) -> str:
    return format_who(heard.log, ctx.arg, budget=ctx.budget)


@command("!freq", aliases=("freqs", "frequency"), help="net")
async def _freq(ctx: Ctx) -> str:
    return format_freq(ctx.arg)


@command("!clearmail", aliases=("mailclear", "unmail"), dm_only=True, icon="📮")
async def _clearmail(ctx: Ctx) -> str:
    # No approval needed: anyone can cancel their own mail
    return clear_mail(ctx.arg, key_id(ctx.dm_key))


@command("!mail", aliases=("msg", "leave"), dm_only=True, icon="📮", help="net")
async def _mail(ctx: Ctx) -> str:
    contact = contact_by_key(ctx.contacts, ctx.dm_key)
    from_name = contact_name(contact) if contact else ctx.dm_key[:6]
    from_id = key_id(ctx.dm_key)
    if not (mail.allowed_users.allowed(from_id) or is_admin(from_id)):
        return ("📮 " if cfg.USE_EMOJI else "") + "!mail is only for approved users. Ask an admin to add you"
    return leave_mail(ctx.arg, from_name, from_id, ctx.contacts)


@command("!help", *(f"!help{t}" for t in HELP_TOPICS), mention=False)
async def _help(ctx: Ctx) -> str:
    return help_text(ctx.cmd[5:] or ctx.arg)


@command("!conv", aliases=("convert", "units"), help="conv")
async def _conv(ctx: Ctx) -> str:
    return convert_units(ctx.arg)


@command("!ohm", aliases=("ohms", "vir", "ohmslaw"), help="conv")
async def _ohm(ctx: Ctx) -> str:
    return ohms_law(ctx.arg)


@command("!res", aliases=("resistor", "colour", "color"), help="conv")
async def _res(ctx: Ctx) -> str:
    return resistor(ctx.arg, budget=ctx.budget)


@command("!roll", aliases=("dice",), help="fun")
async def _roll(ctx: Ctx) -> str:
    return roll_dice(ctx.arg)


@command("!flipacoin", aliases=("flip", "coin"), help="fun")
async def _flip(ctx: Ctx) -> str:
    return flip_coin()


@command("!eightball", aliases=("8ball",), help="fun")
async def _eightball(ctx: Ctx) -> str:
    return eightball(ctx.arg)


@command("!warn", aliases=("warnings",), help="wx")
async def _warn(ctx: Ctx) -> Optional[str]:
    """Also watches the region, so changes are posted back to where the !warn came from."""
    code, warnings, text = await lookup_warnings(ctx.arg, budget=ctx.budget, quiet=True)
    if ctx.target and warnings is not None:
        alerts.watcher.add(ctx.target[0], ctx.target[1], code, warnings)
    return text


def _report_command(name: str, aliases: tuple[str, ...] = (), help: str = "") -> None:
    """A command that replies with REPORTS[name], silent when a place can't be found."""
    report = REPORTS[name]

    async def handler(ctx: Ctx) -> Optional[str]:
        return await report.fetch(ctx.arg, budget=ctx.budget, quiet=True)
    command("!" + name, aliases=aliases, help=help, needs_wx=report.needs_wx)(handler)


_report_command("wx", help="wx")
_report_command("wxh", help="wx")
_report_command("wxf", help="wx")
_report_command("sun", aliases=("sunrise", "sunset"), help="wx")
_report_command("moon", help="wx")
_report_command("aq", aliases=("air",), help="wx")
_report_command("pollen", help="wx")
_report_command("aurora", aliases=("solar",), help="radio")
_report_command("hf", aliases=("bands",), help="radio")
_report_command("vhf", help="radio")
_report_command("uhf", aliases=("tropo",), help="radio")


@command("!stats", admin=True)
async def _stats_cmd(ctx: Ctx) -> str:
    return format_stats(await battery_mv(), budget=ctx.budget)


@command("!uptime", admin=True)
async def _uptime(ctx: Ctx) -> str:
    return format_uptime()


@command("!mute", admin=True)
async def _mute(ctx: Ctx) -> str:
    return mute(ctx.arg)


@command("!unmute", admin=True)
async def _unmute(ctx: Ctx) -> str:
    return unmute()


@command("!say", admin=True)
async def _say(ctx: Ctx) -> str:
    return say(ctx.arg)


@command("!save", admin=True)
async def _save(ctx: Ctx) -> str:
    return save_state()


@command("!addmailuser", aliases=("addmailusers",), admin=True)
async def _addmailuser(ctx: Ctx) -> str:
    return add_mail_user(ctx.arg, ctx.contacts)


@command("!removemailuser", aliases=("removemailusers",), admin=True)
async def _removemailuser(ctx: Ctx) -> str:
    return remove_mail_user(ctx.arg)


@command("!listmailuser", aliases=("listmailusers",), admin=True)
async def _listmailuser(ctx: Ctx) -> str:
    return list_mail_users(ctx.contacts, budget=ctx.budget)


WX_COMMANDS = [name for name, c in COMMANDS.items() if c.needs_wx]


async def run_command(cmd: str, arg: str, sender_name: str, rx_info: dict[str, Any],
                      target: Optional[tuple[str, Any]] = None) -> Optional[str]:
    """target is where the reply goes, ("chan", idx) or ("dm", pubkey prefix). !warn watches it."""
    c = COMMANDS.get(cmd)
    if c is None:
        return None
    ctx = Ctx(cmd, arg, sender_name, rx_info, target, getattr(state.radio, "contacts", None) or {})
    if c.dm_only and not ctx.dm_key:
        icon = f"{c.icon} " if cfg.USE_EMOJI and c.icon else ""
        return f"{ctx.mention}{icon}{cmd} only works in a DM to the bot"
    text = await c.handler(ctx)
    if not text:
        return None
    return ctx.mention + text if c.mention else text
