"""!mail: messages held for people out of range, and who may leave them."""

import logging
import os
import re
import time
from collections import Counter
from typing import Any, Optional

from . import config as cfg
from .text import ago, names_list, plural
from .storage import load_json_list, save_json_list
from .contacts import CHAT_NODE_TYPE, contact_by_key, contact_name, heard_id, HEARD_KEY_CHARS, key_id, KEY_QUERY_RE, match_names, PATH_NAME_CHARS

_LOGGER = logging.getLogger("meshpotato_bot")

# ---------- !mail: messages held for people out of range ----------


class Mailbox:
    """Messages waiting for someone the bot can't reach right now. Each is sent by DM the
    next time the bot hears the recipient, then dropped. Kept in memory, loaded from
    MAIL_FILE at startup and saved to it on exit or by !save."""

    def __init__(self):
        self.messages: list[dict[str, Any]] = []
        self.dirty = False

    def add(self, from_name: str, from_id: str, to_name: str, to_key: str, text: str,
            now: Optional[float] = None) -> str:
        """Queue a message. Returns the reply for the sender."""
        icon = "📮 " if cfg.USE_EMOJI else ""
        to_key = key_id(to_key)
        mine = [m for m in self.messages if m["from_id"] == from_id]
        pair = [m for m in mine if m["to_key"] == to_key]
        if len(pair) >= cfg.MAIL_MAX_PER_PAIR:
            return f"{icon}{to_name} already has {cfg.MAIL_MAX_PER_PAIR} from you waiting, the most allowed"
        if len(mine) >= cfg.MAIL_MAX_PER_SENDER:
            return f"{icon}You have {cfg.MAIL_MAX_PER_SENDER} messages waiting, the most allowed. Try again once some are delivered"
        if len(self.messages) >= cfg.MAIL_MAX_TOTAL:
            return f"{icon}The mailbox is full, try again later"
        self.messages.append({"from": from_name, "from_id": from_id, "to": to_name,
                              "to_key": to_key, "text": text,
                              "at": time.time() if now is None else now})
        self.dirty = True
        return f"{icon}Held for {to_name} ({len(pair) + 1}/{cfg.MAIL_MAX_PER_PAIR}), sent by DM when the bot next hears them"

    def take_for(self, key: str, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """Remove and return up to `limit` messages for a public key the bot just heard,
        oldest first. The rest stay for the next time it is heard. Only a key counts,
        never a name: anyone can take a name, but not a key."""
        key = key_id(key)
        if not key or (limit is not None and limit <= 0):
            return []
        mine = [m for m in self.messages if m["to_key"] == key][:limit]
        if mine:
            taken = {id(m) for m in mine}
            self.messages = [m for m in self.messages if id(m) not in taken]
            self.dirty = True
        return mine

    def waiting_from(self, from_id: str) -> Counter:
        """Recipient name -> messages from this sender still waiting."""
        return Counter(m["to"] for m in self.messages if m["from_id"] == from_id)

    def clear_from(self, from_id: str, to_keys: Optional[set[str]] = None) -> int:
        """Remove this sender's waiting messages, to everyone or only to `to_keys`. Returns how many."""
        def theirs(m):
            return m["from_id"] == from_id and (to_keys is None or m["to_key"] in to_keys)
        n = sum(1 for m in self.messages if theirs(m))
        if n:
            self.messages = [m for m in self.messages if not theirs(m)]
            self.dirty = True
        return n

    def prune(self, now: Optional[float] = None) -> None:
        cutoff = (time.time() if now is None else now) - cfg.MAIL_KEEP_DAYS * 86400
        kept = [m for m in self.messages if m["at"] >= cutoff]
        if len(kept) != len(self.messages):
            _LOGGER.info("Dropped %d undelivered messages older than %g days",
                         len(self.messages) - len(kept), cfg.MAIL_KEEP_DAYS)
            self.messages, self.dirty = kept, True

    def load(self, path: str) -> None:
        fields = ("from", "from_id", "to", "to_key", "text")
        self.messages = [m for m in load_json_list(path)
                         if isinstance(m, dict) and all(isinstance(m.get(f), str) for f in fields)
                         and isinstance(m.get("at"), (int, float))]

    def save(self, path: str) -> bool:
        if not self.dirty:
            return True
        self.dirty = not save_json_list(path, self.messages)
        return not self.dirty


box = Mailbox()


class MailUsers:
    """Public keys allowed to leave !mail, kept in MAIL_USERS_FILE. Admins add and remove
    them by DM. Loaded at startup and written straight away on each change, which is rare."""

    def __init__(self):
        self.users: list[dict[str, Any]] = []      # {"key": full or prefix hex, "name": at add time, "added": time}

    def allowed(self, key: str) -> bool:
        key = key_id(key)
        return bool(key) and any(key_id(u["key"]) == key for u in self.users)

    def find(self, query: str) -> list[dict[str, Any]]:
        q = query.strip().lower()
        return [u for u in self.users if u["key"].startswith(q) or q.startswith(u["key"])] if q else []

    def load(self, path: str) -> None:
        self.users = [u for u in load_json_list(path)
                      if isinstance(u, dict) and isinstance(u.get("key"), str)
                      and re.fullmatch(r"[0-9a-f]{12,64}", u["key"])]

    def save(self, path: str) -> bool:
        return save_json_list(path, self.users)


allowed_users = MailUsers()


def mail_users_path() -> str:
    return cfg.MAIL_USERS_FILE if os.path.isabs(cfg.MAIL_USERS_FILE) else os.path.join(cfg.SCRIPT_DIR, cfg.MAIL_USERS_FILE)


MAIL_USER_KEY_RE = re.compile(r"^[0-9a-f]{12,64}$")


def add_mail_user(arg: str, contacts: dict, users: Optional[MailUsers] = None, path: Optional[str] = None) -> str:
    """!addmailuser <pubkey> (admin): let this key use !mail. Needs at least 12 hex characters."""
    users = allowed_users if users is None else users
    icon = "📮 " if cfg.USE_EMOJI else ""
    key = arg.strip().lower()
    if not MAIL_USER_KEY_RE.match(key):
        return icon + "Use !addmailuser <public key>, at least the first 12 hex characters"
    if users.allowed(key):
        return f"{icon}{key[:HEARD_KEY_CHARS]} can already use !mail"
    contact = contact_by_key(contacts, key_id(key))
    name = contact_name(contact) if contact else ""
    users.users.append({"key": key, "name": name, "added": time.time()})
    if not users.save(mail_users_path() if path is None else path):
        users.users.pop()
        return f"{icon}Couldn't save the mail users file, see the log"
    return f"{icon}{name or key[:HEARD_KEY_CHARS]} can now use !mail ({plural(len(users.users), 'user')})"


def remove_mail_user(arg: str, users: Optional[MailUsers] = None, path: Optional[str] = None) -> str:
    """!removemailuser <pubkey> (admin): the start of the key is enough if only one user matches."""
    users = allowed_users if users is None else users
    icon = "📮 " if cfg.USE_EMOJI else ""
    key = arg.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{4,64}", key):
        return icon + "Use !removemailuser <public key>, or its start. !listmailuser shows them"
    found = users.find(key)
    if not found:
        return f"{icon}No mail user with key {key}"
    if len(found) > 1:
        return f"{icon}{len(found)} mail users start {key}, give more of the key"
    old = list(users.users)
    users.users.remove(found[0])
    if not users.save(mail_users_path() if path is None else path):
        users.users = old
        return f"{icon}Couldn't save the mail users file, see the log"
    return f"{icon}{found[0].get('name') or found[0]['key'][:HEARD_KEY_CHARS]} can no longer use !mail"


def list_mail_users(contacts: dict, users: Optional[MailUsers] = None, budget: Optional[int] = None) -> str:
    """!listmailuser (admin): '📮 3 mail users: Bob b0b0b0b0b0b0, Sam 5a5a5a5a5a5a, 1c2d3e4f5a6b'.
    Names come from the contacts now, else the name when they were added."""
    users = allowed_users if users is None else users
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    icon = "📮 " if cfg.USE_EMOJI else ""
    if not users.users:
        return icon + "No mail users. Add one with !addmailuser <public key>"
    items = []
    for u in users.users:
        contact = contact_by_key(contacts, key_id(u["key"]))
        name = (contact_name(contact) if contact else "") or u.get("name", "")
        short = u["key"][:HEARD_KEY_CHARS]
        items.append(f"{name[:PATH_NAME_CHARS].strip()} {short}" if name else short)
    head = f"{icon}{plural(len(items), 'mail user')}: "
    for n in range(len(items), 0, -1):
        text = head + ", ".join(items[:n]) + (f" +{len(items) - n} more" if n < len(items) else "")
        if len(text.encode("utf-8")) <= budget:
            return text
    return head + f"+{len(items)} more"


def mail_path() -> str:
    return cfg.MAIL_FILE if os.path.isabs(cfg.MAIL_FILE) else os.path.join(cfg.SCRIPT_DIR, cfg.MAIL_FILE)


def format_mail(m: dict[str, Any], now: Optional[float] = None) -> str:
    """The DM a recipient gets: '📬 Alice 2h ago: see you at the hall'."""
    now = time.time() if now is None else now
    icon = "📬 " if cfg.USE_EMOJI else "Mail from "
    return f"{icon}{m['from'][:PATH_NAME_CHARS].strip()} {ago(now - m['at'])} ago: {m['text']}"


MAIL_MENTION_RE = re.compile(r"^@\[([^\]]+)\]\s*(.*)$", re.S)


def mail_recipient(arg: str, contacts: dict) -> tuple[Optional[dict], str, str]:
    """Split '!mail' text into (contact, message, error). The name can be an @[mention],
    a whole contact name (spaces and emoji optional: 'sam base hello'), or the start
    of one name ('sa hello'). Only companions can receive, not repeaters."""
    people = [c for c in contacts.values() if c.get("type") == CHAT_NODE_TYPE and contact_name(c)]
    m = MAIL_MENTION_RE.match(arg.strip())
    if m:
        wanted = heard_id(m.group(1))
        found = [c for c in people if heard_id(contact_name(c)) == wanted]
        if not found:
            return None, "", f"No contact called '{m.group(1)}'"
        return found[0], m.group(2).strip(), ""
    words = arg.split()
    # Longest run of leading words that is a whole contact name
    for n in range(len(words) - 1, 0, -1):
        wanted = heard_id(" ".join(words[:n]))
        found = [c for c in people if heard_id(contact_name(c)) == wanted]
        if len(found) == 1:
            return found[0], " ".join(words[n:]), ""
    if len(words) < 2:
        return None, "", ""
    found = [c for c in people if heard_id(contact_name(c)).startswith(heard_id(words[0]))]
    if len(found) > 1:
        return None, "", f"{len(found)} contacts match '{words[0]}': {names_list([contact_name(c) for c in found])}"
    if not found:
        return None, "", f"No contact called '{words[0]}'"
    return found[0], " ".join(words[1:]), ""


def clear_mail(arg: str, from_id: str, mailbox: Optional[Mailbox] = None) -> str:
    """!clearmail [name]: cancel your own messages that haven't been delivered yet, to
    everyone or to one recipient. The name is matched against the people you have
    mail waiting for (emoji optional), or the start of their key."""
    mailbox = box if mailbox is None else mailbox
    icon = "📮 " if cfg.USE_EMOJI else ""
    mine = [m for m in mailbox.messages if m["from_id"] == from_id]
    if not mine:
        return icon + "You have no messages waiting"
    q = arg.strip()
    if not q:
        n = mailbox.clear_from(from_id)
        return f"{icon}Cleared {plural(n, 'waiting message')}"
    recipients = {m["to_key"]: m["to"] for m in mine}
    rows = [{"key": k, "name": n} for k, n in recipients.items()]
    found = match_names(rows, q, lambda r: heard_id(r["name"])) or         match_names(rows, heard_id(q), lambda r: heard_id(r["name"]))
    if not found and KEY_QUERY_RE.match(q.lower()):
        found = [r for r in rows if r["key"].startswith(key_id(q))]
    if not found:
        waiting = ", ".join(f"{n[:PATH_NAME_CHARS].strip()} {k}" for n, k in mailbox.waiting_from(from_id).most_common(4))
        return f"{icon}No messages waiting for '{q}'. Waiting: {waiting}"
    if len(found) > 1:
        names = names_list([r["name"] for r in found])
        return f"{icon}{len(found)} match '{q}': {names}. Give more of the name"
    n = mailbox.clear_from(from_id, {found[0]["key"]})
    return f"{icon}Cleared {plural(n, 'message')} to {found[0]['name']}"


def leave_mail(arg: str, from_name: str, from_id: str, contacts: dict, mailbox: Optional[Mailbox] = None) -> str:
    """!mail <name> <message>: hold a message until the bot hears them.
    !mail alone shows how to use it and what you have waiting."""
    mailbox = box if mailbox is None else mailbox
    icon = "📮 " if cfg.USE_EMOJI else ""
    if not arg.strip():
        waiting = mailbox.waiting_from(from_id)
        mine = ", ".join(f"{n[:PATH_NAME_CHARS].strip()} {k}" for n, k in waiting.most_common(4))
        return f"{icon}Use !mail <name> <message>" + (f". Waiting: {mine}. !clearmail to cancel" if mine else "")
    contact, text, error = mail_recipient(arg, contacts)
    if error:
        return icon + error
    if not contact or not text:
        return f"{icon}Use !mail <name> <message>"
    if len(text.encode("utf-8")) > cfg.MAIL_MAX_BYTES:
        return f"{icon}Too long, {len(text.encode('utf-8'))} bytes. The most is {cfg.MAIL_MAX_BYTES}"
    key = key_id(contact.get("public_key"))
    return mailbox.add(from_name or "?", from_id, contact_name(contact), key, text)
