#!/usr/bin/env bash
# Install the MeshCore MeshPotato bot as a systemd service (Arch Linux / Arch Linux ARM / Raspberry Pi).
#
# Usage (run as your normal user, not root; it asks for sudo when needed):
#   ./install_mesh_potato_bot_service.sh                         # defaults below
#   ./install_mesh_potato_bot_service.sh --script run_bot.py --port /dev/ttyACM0
#   ./install_mesh_potato_bot_service.sh --python ~/meshbot/bin/python
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

# Stop with a clear message when an option is missing its value
need_value() {
    if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "$1 needs a value" >&2
        exit 1
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --script)    need_value "$@"; SCRIPT="$2"; shift 2 ;;
        --port)      need_value "$@"; PORT="$2"; shift 2 ;;
        --python)    need_value "$@"; PYTHON="$2"; shift 2 ;;
        --name)      need_value "$@"; SERVICE_NAME="$2"; shift 2 ;;
        --uninstall) UNINSTALL=1; shift ;;
        -h|--help)   sed -n '2,12p' "$0"; exit 0 ;;
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
# Accept a bare name such as "python3" as well as a full path
PYTHON_PATH="$(command -v "$PYTHON" 2>/dev/null || true)"
[[ -n "$PYTHON_PATH" && -x "$PYTHON_PATH" ]] || { echo "Python not found: ${PYTHON:-python3}. Install it: sudo pacman -S python" >&2; exit 1; }
PYTHON="$PYTHON_PATH"
# systemd needs an absolute path. -s keeps venv symlinks as they are.
[[ "$PYTHON" == /* ]] || PYTHON="$(realpath -s "$PYTHON")"

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

# API key: the service does not read ~/.bashrc, so it needs the key in
# config.toml or the key file
CONFIG_FILE="${WORK_DIR}/config.toml"
KEY_FILE="${RUN_HOME}/.config/meshcore/metoffice_key"
if [[ -f "$CONFIG_FILE" ]] && grep -Eq '^[[:space:]]*metoffice_api_key[[:space:]]*=[[:space:]]*"[^"]+' "$CONFIG_FILE"; then
    # The file holds a secret, so keep it private
    chmod 600 "$CONFIG_FILE"
    echo "Using the API key in $CONFIG_FILE (set to mode 600)"
elif [[ ! -s "$KEY_FILE" ]]; then
    if [[ -n "${METOFFICE_API_KEY:-}" ]]; then
        mkdir -p "$(dirname "$KEY_FILE")"
        umask 077
        printf '%s\n' "$METOFFICE_API_KEY" > "$KEY_FILE"
        chmod 600 "$KEY_FILE"
        echo "Saved METOFFICE_API_KEY to $KEY_FILE"
    else
        echo "WARNING: no API key found. Set metoffice_api_key in $CONFIG_FILE"
        echo "         or put the key in $KEY_FILE. !wx/!wxh/!wxf fail until you do."
    fi
fi

# Prefer a stable /dev/serial/by-id path if one exists and no port was given
EXEC_START="\"${PYTHON}\" \"${SCRIPT_PATH}\""
if [[ -z "$PORT" ]]; then
    BYID="$(ls /dev/serial/by-id/* 2>/dev/null | head -n1 || true)"
    if [[ -n "$BYID" ]]; then
        PORT="$BYID"
        echo "Using serial port $PORT"
    fi
fi
[[ -n "$PORT" ]] && EXEC_START+=" --port \"${PORT}\""

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
ExecStart=${EXEC_START}
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
echo "  ExecStart: ${EXEC_START}"
echo
echo "Status:   sudo systemctl status ${SERVICE_NAME}"
echo "Logs:     journalctl -u ${SERVICE_NAME} -f"
echo "Restart:  sudo systemctl restart ${SERVICE_NAME}   (after editing the script)"
echo "Remove:   ${INSTALLER_DIR}/install_mesh_potato_bot_service.sh --uninstall"
