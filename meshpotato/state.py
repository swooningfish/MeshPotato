"""What the running bot shares between modules: the radio, its send queue and the mute."""

import time
from typing import TYPE_CHECKING, Optional

from meshcore import MeshCore

if TYPE_CHECKING:
    from .tx import Sender

radio: Optional[MeshCore] = None   # set by Bot.run: contacts for commands, battery for !stats
tx: Optional["Sender"] = None      # set by Bot.run so !say can queue a channel message
muted_until = 0.0                  # time.monotonic() when a !mute ends
channel_names: dict[int, str] = {} # set by Bot.run: channel index to its name on the radio, e.g. 1 -> "#test"


def mute_remaining() -> float:
    """Seconds of mute left, 0 when not muted."""
    return max(0.0, muted_until - time.monotonic())
