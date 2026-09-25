"""JSON files written so a crash mid-write can't leave half a file."""

import json
import logging
import os
from typing import Any

_LOGGER = logging.getLogger("meshpotato_bot")


def load_json_list(path: str) -> list:
    """The list saved in `path`, or [] when there is no file or it can't be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as ex:
        _LOGGER.warning("Can't read %s, starting empty: %s", path, ex)
        return []


def save_json_list(path: str, items: list) -> bool:
    """Write via a temp file so a crash mid-write can't leave half a file. False on failure."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(items, fh)
        os.replace(tmp, path)
        return True
    except OSError as ex:
        _LOGGER.warning("Can't save %s: %s", path, ex)
        return False


def save_part(store: Any, path: str, what: str) -> str:
    """'12 nodes saved', '3 messages unchanged' or 'heard.json failed'."""
    if not store.dirty:
        return f"{what} unchanged"
    return f"{what} saved" if store.save(path) else f"{os.path.basename(path)} failed"
