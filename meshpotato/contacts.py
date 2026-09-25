"""Public keys and names: matching the radio's contacts, and who is an admin."""

import re
from typing import Any, Callable, Optional

from . import config as cfg
from . import state

PATH_NAME_CHARS = 12            # repeater names in !path are cut to this many characters
CHAT_NODE_TYPE = 1              # MeshCore contact type for a companion. Companions don't repeat
HEARD_KEY_CHARS = 12            # public key prefix kept per node, as in a DM's pubkey_prefix


def key_id(key: Optional[str]) -> str:
    """The short lower-case key the bot keeps per node: heard list, mailbox, mail users, admins."""
    return (key or "").strip().lower()[:HEARD_KEY_CHARS]


def contacts_by_key(contacts: dict, prefix: Optional[str]) -> list[dict]:
    """Contacts whose public key starts with `prefix`, any case. None for an empty prefix."""
    prefix = (prefix or "").lower()
    if not prefix:
        return []
    return [c for c in contacts.values() if (c.get("public_key") or "").lower().startswith(prefix)]


def contact_by_key(contacts: dict, prefix: Optional[str]) -> Optional[dict]:
    """The one contact whose key starts with `prefix`. None when none or several match."""
    found = contacts_by_key(contacts, prefix)
    return found[0] if len(found) == 1 else None


def is_admin(key: Optional[str]) -> bool:
    """True for a key in ADMIN_PUBKEYS. Compared on the first HEARD_KEY_CHARS characters."""
    key = key_id(key)
    return bool(key) and key in {key_id(k) for k in cfg.ADMIN_PUBKEYS}


def repeater_names(contacts: Optional[dict] = None) -> dict[str, str]:
    """Public key (lower-case hex) -> name for the radio's contacts that can repeat."""
    if contacts is None:
        contacts = getattr(state.radio, "contacts", None) or {}
    names = {}
    for c in contacts.values():
        key, name = (c.get("public_key") or "").lower(), (c.get("adv_name") or "").strip()
        if key and name and c.get("type") != CHAT_NODE_TYPE:
            names[key] = name
    return names


def match_names(items: list, query: str, name_of: Callable[[Any], str]) -> list:
    """Items whose name is `query` (ignoring case), else starts with it, else contains it."""
    q = " ".join(query.split()).lower()
    if not q:
        return []
    for test in (lambda n: n == q, lambda n: n.startswith(q), lambda n: q in n):
        found = [i for i in items if test(" ".join(name_of(i).split()).lower())]
        if found:
            return found
    return []


def contact_name(c: dict) -> str:
    return (c.get("adv_name") or "").strip()


def contact_by_name(contacts: dict, name: str) -> Optional[dict]:
    """The one contact called exactly `name`, any case. None when none or several match."""
    found = [c for c in contacts.values() if contact_name(c).lower() == name.strip().lower()] if name.strip() else []
    return found[0] if len(found) == 1 else None


def find_contacts(contacts: dict, query: str) -> list[dict]:
    return match_names(list(contacts.values()), query, contact_name)


def heard_id(name: str) -> str:
    """Name with emoji and symbols removed, lower case, so 'Sam 🐬 Base' and 'Sam 🐟 Base'
    are one node. A name with no letters or digits keeps its symbols."""
    plain = " ".join(re.sub(r"[^\w\s]", "", name).split()).lower()
    return plain or " ".join(name.split()).lower()


KEY_QUERY_RE = re.compile(r"^[0-9a-f]{4,64}$")
