"""HTTP helpers and the TTL cache."""

import json
import weakref
import time
import urllib.request
from typing import Any, Callable, Optional, Union

from . import config as cfg


def get(url: str, headers: Optional[dict] = None, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "meshcore-meshpotato-bot/1.0", "Accept": accept, **(headers or {})},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=cfg.HTTP_TIMEOUT) as resp:
        return resp.read()


def get_json(url: str, headers: Optional[dict] = None) -> Any:
    return json.loads(get(url, headers).decode("utf-8"))


class TTLCache:
    """Values kept for `ttl` seconds. Give a function, e.g. lambda: WX_CACHE_SEC, to follow a
    setting that config.toml can change. Every cache is in TTLCache.all for housekeeping."""
    all: "weakref.WeakSet[TTLCache]" = weakref.WeakSet()

    def __init__(self, ttl: Union[float, Callable[[], float]]):
        self._ttl = ttl
        self._data: dict[Any, tuple[float, Any]] = {}
        TTLCache.all.add(self)

    @property
    def ttl(self) -> float:
        return self._ttl() if callable(self._ttl) else self._ttl

    def get(self, key):
        item = self._data.get(key)
        if item and time.monotonic() - item[0] < self.ttl:
            return item[1]
        self._data.pop(key, None)
        return None

    def put(self, key, value):
        self._data[key] = (time.monotonic(), value)

    def cleanup(self) -> None:
        now = time.monotonic()
        for key, (stamp, _) in list(self._data.items()):
            if now - stamp >= self.ttl:
                self._data.pop(key, None)
