"""MeshRank share links for !route: https://meshrank.net/path/65160 shows the route a message took.

MeshRank's observers upload what they hear. The bot looks for the sender's message on the
channel, then asks MeshRank for a share link to it (valid for 24 hours). MeshRank can only
read public and hashtag channels, so there is no link for a private channel or a DM."""

import asyncio
import logging
import time
import urllib.parse
from datetime import datetime
from typing import Callable, Optional

from . import config as cfg
from . import net

_LOGGER = logging.getLogger("meshpotato_bot")

MESHRANK_URL = "https://meshrank.net"
LOOKUP_GAP_SEC = 2          # between lookups while MeshRank hasn't heard the message yet
MAX_AGE_SEC = 600           # only match a message MeshRank heard in the last 10 min


def _age(ts: str) -> float:
    """Seconds since MeshRank's '2026-09-25T21:21:06.124Z', or infinity when it can't be read."""
    try:
        return time.time() - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return float("inf")


def find_message(channel: str, sender: str, matches: Callable[[str], bool]) -> str:
    """The MeshRank id of the sender's latest matching message on the channel, "" if not heard yet."""
    url = f"{MESHRANK_URL}/api/messages?" + urllib.parse.urlencode({"channel": channel, "limit": 20})
    messages = (net.get_json(url) or {}).get("messages") or []
    for m in sorted(messages, key=lambda m: str(m.get("ts", "")), reverse=True):
        if m.get("sender") == sender and matches(m.get("body") or "") and _age(m.get("ts")) < MAX_AGE_SEC:
            return str(m.get("messageHash") or m.get("frameHash") or m.get("id") or "").upper()
    return ""


def share(message_id: str) -> str:
    """'https://meshrank.net/path/65160', or "" when MeshRank won't make one."""
    data = net.post_json(f"{MESHRANK_URL}/api/routes/{urllib.parse.quote(message_id)}/share") or {}
    if not data.get("ok"):
        return ""
    return str(data.get("url") or (f"{MESHRANK_URL}/path/{data['code']}" if data.get("code") else ""))


def _link_sync(channel: str, sender: str, matches: Callable[[str], bool], wait: float) -> str:
    deadline = time.monotonic() + wait
    while True:
        time.sleep(LOOKUP_GAP_SEC)
        message_id = find_message(channel, sender, matches)
        if message_id:
            return share(message_id)
        if time.monotonic() + LOOKUP_GAP_SEC > deadline:
            return ""


async def route_link(channel: str, sender: str, matches: Callable[[str], bool]) -> str:
    """Link to the route the sender's message took, "" when it's off, not found or MeshRank fails."""
    if not (cfg.MESHRANK_LINKS and channel and sender):
        return ""
    try:
        return await asyncio.to_thread(_link_sync, channel, sender, matches, cfg.MESHRANK_WAIT_SEC)
    except Exception as ex:
        _LOGGER.warning("MeshRank link failed: %s", ex)
        return ""
