#!/usr/bin/env bash
# Uninstall the kimico-gateway systemd service.
#
# Usage:
#   sudo ./deploy/uninstall.sh           # remove unit and code, keep config/state
#   sudo ./deploy/uninstall.sh --purge   # also remove config and state
#
# The script never stops or removes NapCat containers and never kills the tmux
# session; those are operator decisions documented in deploy/README.md.

set -euo pipefail

SERVICE_NAME="kimico-gateway"
INSTALL_ROOT="${KIMICO_GATEWAY_INSTALL_ROOT:-/opt/kimico-gateway}"
CONFIG_DIR="${KIMICO_GATEWAY_CONFIG_DIR:-/etc/kimico-gateway}"
STATE_DIR="${KIMICO_GATEWAY_STATE_DIR:-/var/lib/kimico-gateway}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
PURGE=0

usage() {
    sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for argument in "$@"; do
    case "${argument}" in
        --purge)
            PURGE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown argument: ${argument}" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "uninstall.sh must run as root; use sudo." >&2
    exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true
fi
rm -f "${UNIT_PATH}"
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
fi

rm -rf "${INSTALL_ROOT}"

if [ "${PURGE}" -eq 1 ]; then
    rm -rf "${CONFIG_DIR}" "${STATE_DIR}"
    echo "removed unit, code, configuration, and state."
else
    echo "removed unit and code."
    echo "kept configuration and state:"
    echo "  ${CONFIG_DIR}"
    echo "  ${STATE_DIR}"
    echo "run with --purge to remove them as well."
fi
