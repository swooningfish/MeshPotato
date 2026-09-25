"""Text helpers: fit a reply in a MeshCore message, plurals, ages and lists of names."""

from typing import Optional

from . import config as cfg
from .contacts import PATH_NAME_CHARS


def names_list(names: list[str], limit: int = 4, cut: Optional[int] = PATH_NAME_CHARS) -> str:
    """'Alice, Alan, Alfie +2 more'."""
    shown = ", ".join(n[:cut].strip() for n in names[:limit])
    return shown + (f" +{len(names) - limit} more" if len(names) > limit else "")


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def ago(seconds: float) -> str:
    """Short age: '40s', '5m', '3h', '2d'."""
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds // 60:.0f}m"
    if seconds < 172800:
        return f"{seconds // 3600:.0f}h"
    return f"{seconds // 86400:.0f}d"


def trim(text: str, limit: Optional[int] = None) -> str:
    """Collapse whitespace and cut to `limit` UTF-8 bytes without splitting a character."""
    limit = cfg.MAX_REPLY_BYTES if limit is None else limit
    text = " ".join(text.split())
    if len(text.encode("utf-8")) <= limit:
        return text
    out = text.encode("utf-8")[: limit - 1].decode("utf-8", "ignore")
    out = out.rstrip("️‍ ")  # drop a dangling emoji modifier
    return out + "~"


def split_sender(text: str) -> tuple[str, str]:
    """Channel text arrives as 'Sender: message'."""
    if ":" in text:
        sender, body = text.split(":", 1)
        return sender.strip(), body.strip()
    return "", text.strip()


def duration(seconds: float) -> str:
    mins = int(seconds // 60)
    days, mins = divmod(mins, 1440)
    hours, mins = divmod(mins, 60)
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m" if mins else f"{int(seconds)}s"
