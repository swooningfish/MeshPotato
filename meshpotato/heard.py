"""!who and !status: who the bot has heard, kept over restarts."""

import os
import time
from typing import Any, Optional

from . import config as cfg
from .text import ago, names_list
from .storage import load_json_list, save_json_list
from .contacts import contact_name, contacts_by_key, find_contacts, key_id, KEY_QUERY_RE, match_names, PATH_NAME_CHARS
from .rx import hops_text
from .geo import compass_point, distance_text, gps_of, haversine_km, initial_bearing


class Heard:
    """Nodes the bot has heard, keyed by public key: adverts, which are signed, and DMs,
    which are encrypted to the key. So nobody can fake being heard, and a rename updates
    the same entry. Channel messages aren't recorded: they only carry a name, which
    anyone can set. Loaded from HEARD_FILE at startup and saved to it on exit or by !save."""

    def __init__(self):
        self.nodes: dict[str, dict[str, Any]] = {}
        self.dirty = False

    def record(self, name: str, via: str, key: str, info: Optional[dict[str, Any]] = None,
               repeater: bool = False, now: Optional[float] = None) -> None:
        """via is 'dm' or 'advert'. info is the rx info (hops, SNR). Needs a name and a key."""
        name = " ".join((name or "").split())
        key = key_id(key)
        if not name or not key:
            return
        entry: dict[str, Any] = {"name": name, "at": time.time() if now is None else now, "via": via, "key": key}
        if repeater:
            entry["repeater"] = True
        info = info or {}
        if info.get("direct"):
            entry["direct"] = True
        for field in ("path_len", "snr"):
            if info.get(field) is not None:
                entry[field] = info[field]
        self.nodes[key] = entry
        self.dirty = True

    def find(self, query: str) -> list[dict[str, Any]]:
        """Nodes whose name matches, else whose key starts with the query ('!status b0b0')."""
        nodes = list(self.nodes.values())
        found = match_names(nodes, query, lambda e: e["name"])
        q = query.strip().lower()
        if not found and KEY_QUERY_RE.match(q):
            found = [e for e in nodes if e["key"].startswith(q) or q.startswith(e["key"])]
        return found

    def recent(self, hours: float, now: Optional[float] = None, repeaters: bool = False) -> list[dict[str, Any]]:
        """People (or repeaters) heard in the last `hours`, most recent first."""
        cutoff = (time.time() if now is None else now) - hours * 3600
        found = [e for e in self.nodes.values() if e["at"] >= cutoff and bool(e.get("repeater")) == repeaters]
        return sorted(found, key=lambda e: -e["at"])

    def prune(self, now: Optional[float] = None) -> None:
        cutoff = (time.time() if now is None else now) - cfg.HEARD_KEEP_DAYS * 86400
        old = [k for k, e in self.nodes.items() if e["at"] < cutoff]
        for k in old:
            del self.nodes[k]
        self.dirty = self.dirty or bool(old)

    def load(self, path: str) -> None:
        """Entries without a key (channel names, from older versions) are dropped, and the
        file is rewritten without them on the next save."""
        self.nodes = {}
        saved = load_json_list(path)
        for e in saved:
            if (isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"]
                    and isinstance(e.get("key"), str) and KEY_QUERY_RE.match(e["key"])
                    and isinstance(e.get("at"), (int, float))):
                old = self.nodes.get(e["key"])
                if old is None or old["at"] < e["at"]:
                    self.nodes[e["key"]] = e
        self.dirty = len(self.nodes) != len(saved)

    def save(self, path: str) -> bool:
        """Write the list if it changed since the last save. False when the write failed."""
        if not self.dirty:
            return True
        self.dirty = not save_json_list(path, list(self.nodes.values()))
        return not self.dirty


log = Heard()


def heard_path() -> str:
    return cfg.HEARD_FILE if os.path.isabs(cfg.HEARD_FILE) else os.path.join(cfg.SCRIPT_DIR, cfg.HEARD_FILE)


def _via_text(via: str) -> str:
    return "by DM" if via == "dm" else "by advert"


def format_who(heard: Heard, arg: str = "", now: Optional[float] = None, budget: Optional[int] = None) -> str:
    """!who: '👥 4 heard in 24h: Alice 2m, Bob 15m, Carol 3h +1 more'. Only nodes heard by
    advert or DM, so every time is proven by the node's key.
    '!who 2' looks back 2 hours, '!who rpt' lists repeaters instead of people."""
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    now = time.time() if now is None else now
    icon = "👥 " if cfg.USE_EMOJI else ""
    words = arg.lower().split()
    repeaters = any(w in ("rpt", "rpts", "repeater", "repeaters") for w in words)
    hours = cfg.WHO_HOURS
    for w in words:
        try:
            hours = min(max(float(w.rstrip("h")), 0.1), cfg.HEARD_KEEP_DAYS * 24)
        except ValueError:
            pass
    found = heard.recent(hours, now, repeaters=repeaters)
    if not found:
        return f"{icon}No {'repeaters' if repeaters else 'people'} heard in the last {hours:g}h"
    kind = ("repeater " if len(found) == 1 else "repeaters ") if repeaters else ""
    head = f"{icon}{len(found)} {kind}heard in {hours:g}h: "
    # Full names when they fit, else names cut to PATH_NAME_CHARS, else fewer names
    for n in range(len(found), 0, -1):
        more = f" +{len(found) - n} more" if n < len(found) else ""
        for cut in (None, PATH_NAME_CHARS):
            text = head + ", ".join(f"{e['name'][:cut].strip()} {ago(now - e['at'])}" for e in found[:n]) + more
            if len(text.encode("utf-8")) <= budget:
                return text
    return head + f"+{len(found)} more"


def _heard_parts(e: dict[str, Any], now: float) -> list[str]:
    """'heard 12m ago on ch1', '2 hops', 'SNR 7.5dB'."""
    parts = [f"heard {ago(now - e['at'])} ago {_via_text(e['via'])}"]
    if e.get("direct"):
        parts.append("direct route")
    elif e.get("path_len") is not None:
        parts.append(hops_text(e["path_len"]))
    if e.get("snr") is not None:
        parts.append(f"SNR {e['snr']:g}dB")
    return parts


def _person_label(e: dict[str, Any]) -> str:
    """'Bob b0b0', so two nodes with one name can be told apart."""
    return f"{e['name'][:PATH_NAME_CHARS].strip()} {e['key'][:4]}"


def format_status(query: str, heard: Heard, contacts: dict, bot_pos: Optional[tuple[float, float]],
                  now: Optional[float] = None) -> str:
    """!status: '👤 Bob: heard 3h ago by advert | 📍 4.2km NE of bot'. Falls back to the
    contact's last advert when the bot hasn't heard them itself. The query can be a
    name or the start of a public key."""
    now = time.time() if now is None else now
    icon, pin = ("👤 ", "📍 ") if cfg.USE_EMOJI else ("", "")
    q = query.strip()
    if not q:
        return icon + "Use !status <name or key>, e.g. !status Alice. !who lists who's been heard"
    matches = heard.find(q)
    if len(matches) > 1:
        return f"{icon}{len(matches)} match '{q}': {names_list([_person_label(r) for r in matches], cut=None)}"
    contact = None
    sections = []
    if matches:
        e = matches[0]
        name = e["name"]
        sections.append(", ".join(_heard_parts(e, now)))
        contact = next(iter(contacts_by_key(contacts, e["key"])), None)
    else:
        found = find_contacts(contacts, q)
        if not found and KEY_QUERY_RE.match(q.lower()):
            found = contacts_by_key(contacts, q)
        if len(found) > 1:
            return f"{icon}{len(found)} contacts match '{q}': {names_list([contact_name(c) for c in found])}"
        if not found:
            return f"{icon}No one called '{q}' heard"
        contact = found[0]
        name = contact_name(contact)
        advert = contact.get("last_advert") or 0
        # last_advert is the node's own clock, so ignore one that is far in the future
        sections.append(f"last advert {ago(now - advert)} ago" if 0 < advert <= now + 3600
                        else "in contacts, not heard yet")
    pos = gps_of(contact)
    if pos and bot_pos:
        km = haversine_km(bot_pos, pos)
        where = f"{distance_text(km)} {compass_point(initial_bearing(bot_pos, pos))} of bot" if km >= 0.05 else "at the bot"
        sections.append(pin + where)
    return f"{icon}{name}: " + " | ".join(sections)
