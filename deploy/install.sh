#!/usr/bin/env bash
# Install kimico-gateway as a systemd service.
#
# Usage:
#   sudo ./deploy/install.sh [--no-start]
#
# Environment overrides (mainly for testing or non-standard layouts):
#   KIMICO_GATEWAY_USER             service user              (default: ubuntu)
#   KIMICO_GATEWAY_GROUP            service group             (default: ubuntu)
#   KIMICO_GATEWAY_INSTALL_ROOT     code root                 (default: /opt/kimico-gateway)
#   KIMICO_GATEWAY_CONFIG_DIR       config directory          (default: /etc/kimico-gateway)
#   KIMICO_GATEWAY_STATE_DIR        state directory           (default: /var/lib/kimico-gateway)
#   KIMICO_GATEWAY_CONFIG_SOURCE    config to install when missing
#                                   (default: deploy/config.toml.example)
#   KIMICO_GATEWAY_PYTHON           interpreter checked before install
#                                   (default: /usr/bin/python3.12)
#
# The script is idempotent: an existing config.toml is never overwritten, and
# repeated runs only refresh the package and the unit file.

set -euo pipefail

SERVICE_NAME="kimico-gateway"
SERVICE_USER="${KIMICO_GATEWAY_USER:-ubuntu}"
SERVICE_GROUP="${KIMICO_GATEWAY_GROUP:-ubuntu}"
INSTALL_ROOT="${KIMICO_GATEWAY_INSTALL_ROOT:-/opt/kimico-gateway}"
CONFIG_DIR="${KIMICO_GATEWAY_CONFIG_DIR:-/etc/kimico-gateway}"
STATE_DIR="${KIMICO_GATEWAY_STATE_DIR:-/var/lib/kimico-gateway}"
CONFIG_FILE="${CONFIG_DIR}/config.toml"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
PYTHON_BIN="${KIMICO_GATEWAY_PYTHON:-/usr/bin/python3.12}"
START_SERVICE=1

usage() {
    sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for argument in "$@"; do
    case "${argument}" in
        --no-start)
            START_SERVICE=0
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
    echo "install.sh must run as root; use sudo." >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CONFIG_SOURCE="${KIMICO_GATEWAY_CONFIG_SOURCE:-${SCRIPT_DIR}/config.toml.example}"
UNIT_SOURCE="${SCRIPT_DIR}/${SERVICE_NAME}.service"
PACKAGE_SOURCE="${REPO_ROOT}/kimico_gateway"

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "service user does not exist: ${SERVICE_USER}" >&2
    exit 1
fi
if [ ! -x "${PYTHON_BIN}" ]; then
    echo "Python interpreter not found or not executable: ${PYTHON_BIN}" >&2
    exit 1
fi
if [ ! -d "${PACKAGE_SOURCE}" ]; then
    echo "package directory not found: ${PACKAGE_SOURCE}" >&2
    exit 1
fi
if [ ! -f "${CONFIG_SOURCE}" ]; then
    echo "config source not found: ${CONFIG_SOURCE}" >&2
    exit 1
fi
if [ ! -f "${UNIT_SOURCE}" ]; then
    echo "unit file not found: ${UNIT_SOURCE}" >&2
    exit 1
fi

echo "installing ${SERVICE_NAME} for user ${SERVICE_USER}:${SERVICE_GROUP}"

install -d -m 755 "${INSTALL_ROOT}"
rm -rf "${INSTALL_ROOT}/kimico_gateway"
cp -R "${PACKAGE_SOURCE}" "${INSTALL_ROOT}/kimico_gateway"
rm -rf "${INSTALL_ROOT}/kimico_gateway/__pycache__"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}"
chmod -R u=rwX,go=rX "${INSTALL_ROOT}"

install -d -m 750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${CONFIG_DIR}"
install -d -m 750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${STATE_DIR}"

if [ -f "${CONFIG_FILE}" ]; then
    echo "keeping existing configuration: ${CONFIG_FILE}"
else
    install -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 600 \
        "${CONFIG_SOURCE}" "${CONFIG_FILE}"
    if [ "${CONFIG_SOURCE}" = "${SCRIPT_DIR}/config.toml.example" ]; then
        echo "installed config template; edit ${CONFIG_FILE} before first use."
    fi
fi
# Re-assert ownership and mode even when the config already existed.
chown "${SERVICE_USER}:${SERVICE_GROUP}" "${CONFIG_FILE}"
chmod 600 "${CONFIG_FILE}"

install -m 644 "${UNIT_SOURCE}" "${UNIT_PATH}"

if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
    if [ "${START_SERVICE}" -eq 1 ]; then
        if systemctl is-active --quiet "${SERVICE_NAME}"; then
            # Pick up refreshed code without re-enabling the unit.
            systemctl restart "${SERVICE_NAME}"
        else
            systemctl enable --now "${SERVICE_NAME}"
        fi
    else
        systemctl enable "${SERVICE_NAME}"
        echo "service enabled but not started (--no-start)."
    fi
else
    echo "systemctl not found; unit installed but not enabled." >&2
fi

echo "installed:"
echo "  config: ${CONFIG_FILE} (600 ${SERVICE_USER}:${SERVICE_GROUP})"
echo "  state:  ${STATE_DIR}"
echo "  code:   ${INSTALL_ROOT}/kimico_gateway"
echo "  unit:   ${UNIT_PATH}"
