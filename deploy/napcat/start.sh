#!/usr/bin/env bash
# Start (or reuse) the NapCat container for kimico-gateway.
#
# Usage:
#   sudo ./deploy/napcat/start.sh <gateway-qq>
#
# Optional overrides:
#   NAPCAT_IMAGE       image:tag (default: mlikiowa/napcat-docker:v4.8.0)
#   NAPCAT_NAME        container name (default: napcat)
#   NAPCAT_CONFIG_DIR  host config directory (default: /opt/napcat/config)
#   NAPCAT_QQ_DIR      host QQ data directory (default: /opt/napcat/qq)
#
# The container uses host networking so NapCat can reach the gateway on
# 127.0.0.1:8080 and expose the OneBot API on 127.0.0.1:3000. It publishes no
# Docker ports; do not change that to 0.0.0.0.

set -euo pipefail

ACCOUNT="${1:-${NAPCAT_ACCOUNT:-}}"
IMAGE="${NAPCAT_IMAGE:-mlikiowa/napcat-docker:v4.8.0}"
NAME="${NAPCAT_NAME:-napcat}"
CONFIG_DIR="${NAPCAT_CONFIG_DIR:-/opt/napcat/config}"
QQ_DIR="${NAPCAT_QQ_DIR:-/opt/napcat/qq}"
GATEWAY_CONFIG="${KIMICO_GATEWAY_CONFIG:-/etc/kimico-gateway/config.toml}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ "$(id -u)" -ne 0 ]; then
    echo "start.sh must run as root; use sudo." >&2
    exit 1
fi
if [ -z "${ACCOUNT}" ]; then
    echo "usage: sudo $0 <gateway-qq>" >&2
    exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "docker is not installed" >&2
    exit 1
fi
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "python3 is required to align tokens and ports" >&2
    exit 1
fi

mkdir -p "${CONFIG_DIR}" "${QQ_DIR}"

CONFIGURE_ARGS=(
    --config "${GATEWAY_CONFIG}"
    --gateway-qq "${ACCOUNT}"
    --napcat-dir "${CONFIG_DIR}"
)
if [ -n "${NAPCAT_HTTP_PORT:-}" ]; then
    CONFIGURE_ARGS+=(--port "${NAPCAT_HTTP_PORT}")
fi
if [ -n "${KIMICO_COMMAND:-}" ]; then
    CONFIGURE_ARGS+=(--kimico "${KIMICO_COMMAND}")
fi
"${PYTHON_BIN}" "${SCRIPT_DIR}/configure.py" "${CONFIGURE_ARGS[@]}"

if command -v systemctl >/dev/null 2>&1 \
    && systemctl is-active --quiet kimico-gateway; then
    systemctl restart kimico-gateway
    echo "kimico-gateway restarted with the new token/port"
fi

if docker inspect "${NAME}" >/dev/null 2>&1; then
    if [ "$(docker inspect -f '{{.State.Running}}' "${NAME}")" = "true" ]; then
        docker restart "${NAME}" >/dev/null
        echo "container ${NAME} restarted with the new OneBot config"
    else
        docker start "${NAME}" >/dev/null
        echo "container ${NAME} started"
    fi
    echo "logs: sudo docker logs -f ${NAME}"
    exit 0
fi

docker run -d \
    --name "${NAME}" \
    --restart unless-stopped \
    --network host \
    -v "${CONFIG_DIR}:/app/napcat/config" \
    -v "${QQ_DIR}:/app/.config/QQ" \
    -e ACCOUNT="${ACCOUNT}" \
    "${IMAGE}" >/dev/null

echo "container ${NAME} started"
echo "scan the QR code: sudo docker logs -f ${NAME}"
