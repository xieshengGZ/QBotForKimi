#!/usr/bin/env bash
# Install kimico as a single systemd service.
#
# Usage:
#   sudo ./deploy/install.sh [--no-start]
#
# The service starts the gateway, tmux/kimi, and the NapCat Docker container
# together. The operator only edits the two QQ numbers in
# /etc/kimico/config.toml.
#
# Environment overrides (mainly for testing or non-standard layouts):
#   KIMICO_USER             service user              (default: ubuntu)
#   KIMICO_GROUP            service group             (default: ubuntu)
#   KIMICO_INSTALL_ROOT     code root                 (default: /opt/kimico)
#   KIMICO_CONFIG_DIR       config directory          (default: /etc/kimico)
#   KIMICO_STATE_DIR        state directory           (default: /var/lib/kimico)
#   KIMICO_CONFIG_SOURCE    config to install when missing
#                           (default: deploy/config.toml.example)
#   KIMICO_PYTHON           interpreter checked before install
#                           (default: /usr/bin/python3.12)
#
# The script is idempotent: an existing config.toml is never overwritten, and
# repeated runs only refresh the package and the unit file.

set -euo pipefail

SERVICE_NAME="kimico"
LEGACY_SERVICE_NAME="kimico-gateway"
SERVICE_USER="${KIMICO_USER:-ubuntu}"
SERVICE_GROUP="${KIMICO_GROUP:-ubuntu}"
INSTALL_ROOT="${KIMICO_INSTALL_ROOT:-/opt/kimico}"
CONFIG_DIR="${KIMICO_CONFIG_DIR:-/etc/kimico}"
STATE_DIR="${KIMICO_STATE_DIR:-/var/lib/kimico}"
CONFIG_FILE="${CONFIG_DIR}/config.toml"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
LEGACY_UNIT_PATH="/etc/systemd/system/${LEGACY_SERVICE_NAME}.service"
PYTHON_BIN="${KIMICO_PYTHON:-/usr/bin/python3.12}"
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
CONFIG_SOURCE="${KIMICO_CONFIG_SOURCE:-${SCRIPT_DIR}/config.toml.example}"
UNIT_SOURCE="${SCRIPT_DIR}/${SERVICE_NAME}.service"
PACKAGE_SOURCE="${REPO_ROOT}/kimico_gateway"
NAPCAT_TEMPLATE="${SCRIPT_DIR}/napcat/onebot11.template.json"

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "service user does not exist: ${SERVICE_USER}" >&2
    exit 1
fi
SERVICE_HOME="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
if [ -z "${SERVICE_HOME}" ]; then
    echo "cannot determine home directory for ${SERVICE_USER}" >&2
    exit 1
fi
if [ ! -x "${PYTHON_BIN}" ]; then
    echo "Python interpreter not found or not executable: ${PYTHON_BIN}" >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required (NapCat runs as a managed container)." >&2
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
if [ ! -f "${NAPCAT_TEMPLATE}" ]; then
    echo "NapCat template not found: ${NAPCAT_TEMPLATE}" >&2
    exit 1
fi

# One service only: retire the old split deployment unit if it is still around.
if command -v systemctl >/dev/null 2>&1; then
    systemctl stop "${LEGACY_SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${LEGACY_SERVICE_NAME}" 2>/dev/null || true
fi
rm -f "${LEGACY_UNIT_PATH}"

echo "installing ${SERVICE_NAME} for user ${SERVICE_USER}:${SERVICE_GROUP}"

install -d -m 755 "${INSTALL_ROOT}"
rm -rf "${INSTALL_ROOT}/kimico_gateway"
cp -R "${PACKAGE_SOURCE}" "${INSTALL_ROOT}/kimico_gateway"
rm -rf "${INSTALL_ROOT}/kimico_gateway/__pycache__"
install -d -m 755 "${INSTALL_ROOT}/napcat"
install -m 644 "${NAPCAT_TEMPLATE}" "${INSTALL_ROOT}/napcat/onebot11.template.json"
install -m 644 "${SCRIPT_DIR}/README.md" "${INSTALL_ROOT}/README.md"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_ROOT}"
chmod -R u=rwX,go=rX "${INSTALL_ROOT}"

install -d -m 750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${CONFIG_DIR}"
install -d -m 750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${STATE_DIR}"
install -d -m 750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" \
    "${STATE_DIR}/napcat/config" "${STATE_DIR}/napcat/qq"

if [ -f "${CONFIG_FILE}" ]; then
    echo "keeping existing configuration: ${CONFIG_FILE}"
else
    install -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 600 \
        "${CONFIG_SOURCE}" "${CONFIG_FILE}"
    echo "installed configuration: ${CONFIG_FILE}"
fi
# Re-assert ownership and mode even when the config already existed.
chown "${SERVICE_USER}:${SERVICE_GROUP}" "${CONFIG_FILE}"
chmod 600 "${CONFIG_FILE}"

CONFIG_READY=0
if "${PYTHON_BIN}" - "${CONFIG_FILE}" <<'PY'
import sys
import tomllib

try:
    with open(sys.argv[1], "rb") as handle:
        raw = tomllib.load(handle)
except Exception as exc:
    print(f"cannot read config: {exc}", file=sys.stderr)
    raise SystemExit(1)

qq = raw.get("qq", {})
gateway = qq.get("gateway_qq")
main = qq.get("main_qq")
if isinstance(gateway, bool) or not isinstance(gateway, int) or gateway <= 0:
    print("qq.gateway_qq is missing or invalid", file=sys.stderr)
    raise SystemExit(1)
if isinstance(main, bool) or not isinstance(main, int) or main <= 0:
    print("qq.main_qq is missing or invalid", file=sys.stderr)
    raise SystemExit(1)
if gateway == main:
    print("qq.gateway_qq and qq.main_qq must differ", file=sys.stderr)
    raise SystemExit(1)
if gateway == 1000000000 or main == 1000000001:
    print("config still contains a placeholder QQ number", file=sys.stderr)
    raise SystemExit(1)
PY
then
    CONFIG_READY=1
fi

# The unit template targets the default ubuntu layout; render the configured
# user/home so KIMICO_USER and non-/home/ubuntu servers work too.
sed \
    -e "s#^User=ubuntu#User=${SERVICE_USER}#" \
    -e "s#^Group=ubuntu#Group=${SERVICE_GROUP}#" \
    -e "s#/home/ubuntu#${SERVICE_HOME}#g" \
    -e "s#/opt/kimico#${INSTALL_ROOT}#g" \
    -e "s#/etc/kimico#${CONFIG_DIR}#g" \
    -e "s#/var/lib/kimico#${STATE_DIR}#g" \
    -e "s#ubuntu:ubuntu#${SERVICE_USER}:${SERVICE_GROUP}#g" \
    "${UNIT_SOURCE}" > "${UNIT_PATH}"
chmod 644 "${UNIT_PATH}"

if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
    if [ "${START_SERVICE}" -eq 1 ] && [ "${CONFIG_READY}" -eq 1 ]; then
        if systemctl is-active --quiet "${SERVICE_NAME}"; then
            systemctl restart "${SERVICE_NAME}"
        else
            systemctl enable --now "${SERVICE_NAME}"
        fi
    else
        systemctl enable "${SERVICE_NAME}"
        echo "service enabled but not started."
    fi
else
    echo "systemctl not found; unit installed but not enabled." >&2
fi

echo "installed:"
echo "  config: ${CONFIG_FILE} (600 ${SERVICE_USER}:${SERVICE_GROUP})"
echo "  state:  ${STATE_DIR}"
echo "  code:   ${INSTALL_ROOT}/kimico_gateway"
echo "  unit:   ${UNIT_PATH}"

if [ "${CONFIG_READY}" -eq 0 ]; then
    echo
    echo "next:"
    echo "  1. sudo editor ${CONFIG_FILE}   # fill gateway_qq and main_qq"
    echo "  2. sudo systemctl restart ${SERVICE_NAME}"
    echo "  3. sudo docker logs -f kimico-napcat   # scan the QR code"
elif [ "${START_SERVICE}" -eq 1 ]; then
    echo
    echo "scan the gateway QQ on first start:"
    echo "  sudo docker logs -f kimico-napcat"
fi
