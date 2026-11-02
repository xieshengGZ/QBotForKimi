#!/usr/bin/env bash
# Uninstall the kimico systemd service.
#
# Usage:
#   sudo ./deploy/uninstall.sh           # remove unit/code/container, keep data
#   sudo ./deploy/uninstall.sh --purge   # also remove config and state
#
# Data kept in the default mode includes the generated runtime config, the
# mirror state, and the NapCat QQ login under /var/lib/kimico. --purge deletes
# all of it, so the gateway QQ must scan a QR code again on the next install.

set -euo pipefail

SERVICE_NAME="kimico"
LEGACY_SERVICE_NAME="kimico-gateway"
INSTALL_ROOT="${KIMICO_INSTALL_ROOT:-/opt/kimico}"
CONFIG_DIR="${KIMICO_CONFIG_DIR:-/etc/kimico}"
STATE_DIR="${KIMICO_STATE_DIR:-/var/lib/kimico}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
LEGACY_UNIT_PATH="/etc/systemd/system/${LEGACY_SERVICE_NAME}.service"
NAPCAT_CONTAINER="kimico-napcat"
PURGE=0

usage() {
    sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
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
    systemctl disable --now "${LEGACY_SERVICE_NAME}" 2>/dev/null || true
fi
rm -f "${UNIT_PATH}" "${LEGACY_UNIT_PATH}"
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
fi

if command -v docker >/dev/null 2>&1; then
    docker rm -f "${NAPCAT_CONTAINER}" >/dev/null 2>&1 || true
fi

rm -rf "${INSTALL_ROOT}"

if [ "${PURGE}" -eq 1 ]; then
    rm -rf "${CONFIG_DIR}" "${STATE_DIR}"
    echo "removed unit, code, container, configuration, and state."
else
    echo "removed unit, code, and container."
    echo "kept configuration, state, and NapCat login:"
    echo "  ${CONFIG_DIR}"
    echo "  ${STATE_DIR}"
    echo "run with --purge to remove them as well."
fi
