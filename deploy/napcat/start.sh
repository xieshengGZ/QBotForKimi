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

mkdir -p "${CONFIG_DIR}" "${QQ_DIR}"

if docker inspect "${NAME}" >/dev/null 2>&1; then
    if [ "$(docker inspect -f '{{.State.Running}}' "${NAME}")" = "true" ]; then
        echo "container ${NAME} is already running"
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
