#!/usr/bin/env python3
"""List the channels configured on a MeshCore device.

Reads serial_port and baudrate from config.toml (same file as run_bot.py).
A port given on the command line wins over config.toml.

    python3 channel_list.py [port]
"""

import asyncio
import os
import sys

from meshcore import MeshCore
from meshcore.events import EventType

try:
    import tomllib                  # Python 3.11+
except ModuleNotFoundError:         # Python 3.10: config.toml is not supported
    tomllib = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("MESHPOTATO_CONFIG") or os.path.join(SCRIPT_DIR, "config.toml")

# Defaults, overridden by config.toml
SERIAL_PORT = "/dev/ttyACM0"
BAUDRATE = 115200

# Used when the device doesn't report how many channel slots it has
FALLBACK_MAX_CHANNELS = 40


def load_config():
    """Return (port, baudrate) from config.toml, falling back to the defaults."""
    port, baudrate = SERIAL_PORT, BAUDRATE
    if not os.path.isfile(CONFIG_FILE):
        return port, baudrate
    if tomllib is None:
        raise SystemExit(f"{CONFIG_FILE} needs Python 3.11 or newer (tomllib)")
    try:
        with open(CONFIG_FILE, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as ex:
        raise SystemExit(f"Can't read {CONFIG_FILE}: {ex}")
    port = str(data.get("serial_port", port))
    baudrate = int(data.get("baudrate", baudrate))
    return port, baudrate


async def max_channels(mc):
    """Number of channel slots the device reports, or None if it doesn't say."""
    try:
        result = await mc.commands.send_device_query()
        if result.type == EventType.DEVICE_INFO:
            n = result.payload.get("max_channels")
            if n:
                return int(n)
    except Exception:
        pass
    return None


def is_empty(payload):
    """An unused slot has no name and an all-zero secret."""
    secret = payload.get("channel_secret", b"")
    return not payload.get("channel_name") and not any(secret)


async def list_channels(mc):
    limit = await max_channels(mc)
    known_limit = limit is not None
    limit = limit or FALLBACK_MAX_CHANNELS

    found = 0
    for idx in range(limit):
        result = await mc.commands.get_channel(idx)
        if result.type == EventType.ERROR:
            if known_limit:
                print(f"  {idx:>3}  <error: {result.payload}>")
                continue
            break               # past the last slot the device has
        if result.type != EventType.CHANNEL_INFO:
            print(f"  {idx:>3}  <unexpected response: {result.type}>")
            continue
        payload = result.payload
        if is_empty(payload):
            continue
        print(f"  {payload.get('channel_idx', idx):>3}  {payload.get('channel_name', '')}")
        found += 1

    if not found:
        print("  (no channels configured)")


async def main():
    port, baudrate = load_config()
    if len(sys.argv) > 1:
        port = sys.argv[1]

    print(f"Connecting to device on {port} at {baudrate} baud...")
    mc = None
    try:
        mc = await MeshCore.create_serial(port, baudrate)
        if mc is None:
            raise SystemExit(f"Could not connect to {port}")

        print(f"Device: {mc.self_info.get('adv_name', 'Unknown')}")
        print(f"Public Key: {mc.self_info.get('public_key', 'Unknown')}")
        print()
        print("Channels:")
        await list_channels(mc)
    finally:
        if mc is not None:
            await mc.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
