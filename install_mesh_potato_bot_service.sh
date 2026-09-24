#!/usr/bin/env bash
# Install the MeshCore Meshpotato bot as a systemd service (Arch Linux / Arch Linux ARM / Raspberry Pi).
#
# Usage (run as your normal user, not root; it asks for sudo when needed):
#   ./install_mesh_potato_bot_service.sh                         # defaults below
#   ./install_mesh_potato_bot_service.sh --script run_bot.py --port /dev/ttyACM0
#   ./install_mesh_potato_bot_service.sh --uninstall
#
# After install:
#   sudo systemctl status meshcore-meshpotato-bot
#   journalctl -u meshcore-meshpotato-bot -f
set -euo pipefail

SERVICE_NAME="meshcore-meshpotato-bot"
SCRIPT="run_bot.py"
PORT=""
PYTHON="$(command -v python3 || true)"
UNINSTALL=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --script)    SCRIPT="$2"; shift 2 ;;
        --port)      PORT="$2"; shift 2 ;;
        --python)    PYTHON="$2"; shift 2 ;;
        --name)      SERVICE_NAME="$2"; shift 2 ;;
        --uninstall) UNINSTALL=1; shift ;;
        -h|--help)   sed -n '2,11p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal user (e.g. alarm), not root. It calls sudo itself." >&2
    exit 1
fi

if [[ $UNINSTALL -eq 1 ]]; then
    sudo systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true
    sudo rm -f "${UNIT_PATH}"
    sudo systemctl daemon-reload
    echo "Removed ${SERVICE_NAME}."
    exit 0
fi

# Resolve a relative --script against the current folder first, then the
# folder this installer lives in, so it works from anywhere in the clone.
INSTALLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$SCRIPT" != /* && ! -f "$SCRIPT" && -f "${INSTALLER_DIR}/${SCRIPT}" ]]; then
    SCRIPT="${INSTALLER_DIR}/${SCRIPT}"
fi

RUN_USER="$(id -un)"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
SCRIPT_PATH="$(realpath "$SCRIPT")"
WORK_DIR="$(dirname "$SCRIPT_PATH")"

# ---------- checks ----------
[[ -f "$SCRIPT_PATH" ]] || { echo "Script not found: $SCRIPT_PATH" >&2; exit 1; }
[[ -x "$PYTHON" ]] || { echo "python3 not found. Install it: sudo pacman -S python" >&2; exit 1; }

if ! "$PYTHON" -c "import meshcore" 2>/dev/null; then
    echo "The meshcore package is not importable by $PYTHON." >&2
    echo "Use --python /path/to/venv/bin/python if you installed it in a venv." >&2
    exit 1
fi

# Serial group (uucp on Arch, dialout on Debian/Raspberry Pi OS)
SERIAL_GROUP="uucp"
getent group uucp >/dev/null || SERIAL_GROUP="dialout"
if ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx "$SERIAL_GROUP"; then
    echo "Adding $RUN_USER to the $SERIAL_GROUP group for serial access."
    sudo usermod -aG "$SERIAL_GROUP" "$RUN_USER"
fi

# API key: the service does not read ~/.bashrc, so store it in the key file
KEY_FILE="${RUN_HOME}/.config/meshcore/metoffice_key"
if [[ ! -s "$KEY_FILE" ]]; then
    if [[ -n "${METOFFICE_API_KEY:-}" ]]; then
        mkdir -p "$(dirname "$KEY_FILE")"
        umask 077
        printf '%s\n' "$METOFFICE_API_KEY" > "$KEY_FILE"
        chmod 600 "$KEY_FILE"
        echo "Saved METOFFICE_API_KEY to $KEY_FILE"
    else
        echo "WARNING: no API key in $KEY_FILE and METOFFICE_API_KEY is not set."
        echo "         wx/wxf will fail until you create that file."
    fi
fi

# Prefer a stable /dev/serial/by-id path if one exists and no port was given
PORT_ARG=""
if [[ -z "$PORT" ]]; then
    BYID="$(ls /dev/serial/by-id/* 2>/dev/null | head -n1 || true)"
    if [[ -n "$BYID" ]]; then
        PORT="$BYID"
        echo "Using serial port $PORT"
    fi
fi
[[ -n "$PORT" ]] && PORT_ARG=" --port ${PORT}"

# ---------- write unit ----------
TMP_UNIT="$(mktemp)"
cat > "$TMP_UNIT" <<EOF
[Unit]
Description=MeshCore MeshPotato bot
After=network-online.target time-sync.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
SupplementaryGroups=${SERIAL_GROUP}
WorkingDirectory=${WORK_DIR}
Environment=HOME=${RUN_HOME}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} ${SCRIPT_PATH}${PORT_ARG}
Restart=always
RestartSec=15
KillSignal=SIGINT
TimeoutStopSec=15

# Light hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF

sudo install -m 644 "$TMP_UNIT" "$UNIT_PATH"
rm -f "$TMP_UNIT"

# ModemManager grabs /dev/ttyACM* devices and blocks the radio
if systemctl is-active --quiet ModemManager 2>/dev/null; then
    echo "ModemManager is running and can grab the radio. Disabling it."
    sudo systemctl disable --now ModemManager
fi

sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

echo
echo "Installed ${UNIT_PATH}"
echo "  ExecStart: ${PYTHON} ${SCRIPT_PATH}${PORT_ARG}"
echo
echo "Status:   sudo systemctl status ${SERVICE_NAME}"
echo "Logs:     journalctl -u ${SERVICE_NAME} -f"
echo "Restart:  sudo systemctl restart ${SERVICE_NAME}   (after editing the script)"
echo "Remove:   ${INSTALLER_DIR}/install_mesh_potato_bot_service.sh --uninstall"
