"""Path and signal info: hops, SNR, RSSI and the repeaters a message came through."""

import re
import time
from typing import Any, Optional

from . import config as cfg
from .contacts import PATH_NAME_CHARS

RX_LOG_MAX_AGE_SEC = 5.0        # an RX log older than this can't be the packet that carried a message
DIRECT_PATH_LEN = 0xFF          # path_len of a message that arrived by direct route

# Last RX_LOG_DATA seen: path_len, path_nodes, snr, rssi and its monotonic time "at"
latest_rx: dict[str, Any] = {}


def _split_path(path_hex: str, hash_size: int) -> list[str]:
    step = max(1, hash_size) * 2
    return [path_hex[i:i + step] for i in range(0, len(path_hex), step)]


def _parse_raw_packet(hex_str: str) -> dict[str, Any]:
    """Read the path from a raw packet: header, [4 transport code bytes], path byte, path.
    The path byte holds the hop count (low 6 bits) and hash size - 1 (top 2 bits)."""
    try:
        data = bytes.fromhex(re.sub(r"\s", "", hex_str))
        i = 1
        if data[0] & 0x03 in (0x00, 0x03):      # transport flood / transport direct
            i += 4
        path_byte = data[i]
        hash_size = (path_byte >> 6) + 1
        path_len = path_byte & 0x3F
        path = data[i + 1:i + 1 + path_len * hash_size]
        if len(path) < path_len * hash_size:
            return {}
        return {"path_len": path_len, "path_nodes": _split_path(path.hex(), hash_size)}
    except (ValueError, IndexError):
        return {}


def parse_rx_log_data(payload: Any) -> dict[str, Any]:
    """Path and signal info from an RX_LOG_DATA payload.
    Uses the fields meshcore has already parsed, else decodes the raw packet."""
    if not isinstance(payload, dict):
        return {}
    if payload.get("path_len") is not None:
        result = {"path_len": payload["path_len"],
                  "path_nodes": _split_path(payload.get("path") or "", payload.get("path_hash_size") or 1)}
    else:
        raw = payload.get("payload")
        result = _parse_raw_packet(raw.hex() if isinstance(raw, bytes) else str(raw or ""))
    for key in ("snr", "rssi"):
        if payload.get(key) is not None:
            result[key] = payload[key]
    return result


def message_rx_info(msg: dict[str, Any]) -> dict[str, Any]:
    """Path and signal info for a received message.
    Fields on the message win. The last RX log only fills gaps when it is recent
    and has the same hop count, so it is most likely the packet that carried it."""
    info: dict[str, Any] = {}
    path_len = msg.get("path_len")
    hash_mode = msg.get("path_hash_mode") or 0
    if path_len == DIRECT_PATH_LEN or (path_len == 0x3F and hash_mode == 3):
        info["direct"] = True
        path_len = None
    rx = latest_rx if time.monotonic() - latest_rx.get("at", 0) <= RX_LOG_MAX_AGE_SEC else {}
    if path_len is not None and rx.get("path_len") not in (None, path_len):
        rx = {}
    if not info:
        info["path_len"] = path_len if path_len is not None else rx.get("path_len")
        if msg.get("path"):
            info["path_nodes"] = _split_path(msg["path"], hash_mode + 1)
        elif rx.get("path_nodes"):
            info["path_nodes"] = rx["path_nodes"]
    info["snr"] = msg.get("SNR", rx.get("snr"))
    info["rssi"] = msg.get("RSSI", rx.get("rssi"))
    return info


def hops_text(n: int) -> str:
    return f"{n} hop{'' if n == 1 else 's'}"


def format_hops(info: dict[str, Any]) -> str:
    """ping: '(2 hops)'."""
    if info.get("direct"):
        return "(direct route)"
    n = info.get("path_len")
    return f"({hops_text(n)})" if n is not None else "(? hops)"


def format_rx_report(info: dict[str, Any]) -> str:
    """test: '🐸 (2 hops) 📶 SNR 7.5dB 〰️ RSSI -85dBm', plain '(2 hops) SNR 7.5dB RSSI -85dBm'.
    The path addresses are left out, because a long path pushes the reply past MAX_REPLY_BYTES."""
    parts = [("🐸 " if cfg.USE_EMOJI else "") + format_hops(info)]
    snr, rssi = ("📶 SNR", "〰️ RSSI") if cfg.USE_EMOJI else ("SNR", "RSSI")
    if info.get("snr") is not None:
        parts.append(f"{snr} {info['snr']:g}dB")
    if info.get("rssi") is not None:
        parts.append(f"{rssi} {info['rssi']}dBm")
    return " ".join(parts)


def _node_label(node: str, names: dict[str, str]) -> str:
    """'a1 Norwich' when exactly one known repeater's key starts with the hash, else 'a1'."""
    matches = [n for key, n in names.items() if key.startswith(node.lower())]
    if len(matches) != 1:
        return node
    return f"{node} {matches[0][:PATH_NAME_CHARS].strip()}"


def format_path(info: dict[str, Any], names: Optional[dict[str, str]] = None,
                budget: Optional[int] = None) -> str:
    """!path: '🛤️ 3 hops: a1 Norwich › b2 › c3', first repeater first.
    Leaves the names out if they don't fit, then the last repeaters ('+2 more')."""
    budget = cfg.MAX_REPLY_BYTES if budget is None else budget
    icon, sep = ("🛤️ ", " › ") if cfg.USE_EMOJI else ("Path ", " > ")
    if info.get("direct"):
        return f"{icon}Direct route, the path isn't carried in the message"
    n = info.get("path_len")
    if n is None:
        return f"{icon}Path unknown"
    if n == 0:
        return f"{icon}0 hops, heard directly"
    nodes = info.get("path_nodes") or []
    if not nodes:
        return f"{icon}{hops_text(n)}, path not reported"
    head = f"{icon}{hops_text(n)}: "
    for labels in ([_node_label(x, names or {}) for x in nodes], nodes):
        text = head + sep.join(labels)
        if len(text.encode("utf-8")) <= budget:
            return text
    shown = []
    for i, node in enumerate(nodes):
        more = f" +{len(nodes) - i - 1} more" if i < len(nodes) - 1 else ""
        if len((head + sep.join(shown + [node]) + more).encode("utf-8")) > budget:
            break
        shown.append(node)
    return head + sep.join(shown) + f" +{len(nodes) - len(shown)} more"
