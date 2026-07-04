#!/usr/bin/env bash
#
# Build and deploy JDSSArrow (FastAPI backend + React dashboard) as a single
# Docker container serving the API and UI on one port.
#
# Usage:
#   ./deploy.sh                 # build image and (re)start the container
#   ./deploy.sh build           # build the image only
#   ./deploy.sh up              # (re)start the container from the current image
#   ./deploy.sh down            # stop and remove the container
#   ./deploy.sh logs            # follow container logs
#   ./deploy.sh status          # show container status
#
# Configuration (environment variables):
#   IMAGE       image tag to build/run           (default: jdssarrow:latest)
#   CONTAINER   container name                    (default: jdssarrow)
#   PORT        host port for the dashboard/API   (default: 8000)
#   EUD_PORT    host port for the built-in ATAK/EUD TAK server (default: 8087; "" to disable)
#   CONFIG      path to a JDSS YAML/TOML config   (optional; mounted read-only)
#
# Examples:
#   PORT=9000 ./deploy.sh
#   EUD_PORT=8089 ./deploy.sh
#   CONFIG=examples/node-a.yaml ./deploy.sh

set -euo pipefail

IMAGE="${IMAGE:-jdssarrow:latest}"
CONTAINER="${CONTAINER:-jdssarrow}"
PORT="${PORT:-8000}"
EUD_PORT="${EUD_PORT:-8087}"
CONFIG="${CONFIG:-}"

# Resolve the repo root (directory of this script) so it works from anywhere.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
err() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; }

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    err "docker is not installed or not on PATH."
    err "Install it on Ubuntu with:  curl -fsSL https://get.docker.com | sudo sh"
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    err "cannot talk to the Docker daemon."
    err "Is it running ('sudo systemctl start docker'), and is your user in the 'docker'"
    err "group ('sudo usermod -aG docker \$USER' then re-login) — or re-run with sudo?"
    exit 1
  fi
}

build() {
  # The Dockerfile uses BuildKit features (syntax directive + cache mounts). BuildKit is the
  # default on modern Docker, but force it on so older Ubuntu 'docker.io' packages work too.
  export DOCKER_BUILDKIT=1
  log "Building image ${IMAGE} ..."
  docker build -t "${IMAGE}" .
  log "Built ${IMAGE}."
}

down() {
  if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
    log "Stopping and removing existing container ${CONTAINER} ..."
    docker rm -f "${CONTAINER}" >/dev/null
  fi
}

up() {
  down

  local args=(
    -d
    --name "${CONTAINER}"
    --restart unless-stopped
    -p "${PORT}:8000"
  )

  # publish the built-in ATAK/EUD TAK server port so devices can reach it (matches its config)
  if [[ -n "${EUD_PORT}" ]]; then
    args+=( -p "${EUD_PORT}:${EUD_PORT}" )
  fi

  if [[ -n "${CONFIG}" ]]; then
    if [[ ! -f "${CONFIG}" ]]; then
      err "CONFIG file '${CONFIG}' does not exist."
      exit 1
    fi
    local abs_config
    abs_config="$(cd "$(dirname "${CONFIG}")" && pwd)/$(basename "${CONFIG}")"
    log "Mounting config ${abs_config} -> /config/node.yaml"
    args+=( -v "${abs_config}:/config/node.yaml:ro" -e "JDSS_CONFIG=/config/node.yaml" )
  fi

  log "Starting container ${CONTAINER} on http://localhost:${PORT} ..."
  docker run "${args[@]}" "${IMAGE}" >/dev/null
  log "Deployed. Dashboard: http://localhost:${PORT}  ·  Metrics: http://localhost:${PORT}/metrics"
  if [[ -n "${EUD_PORT}" ]]; then
    log "ATAK/EUD TAK server port ${EUD_PORT} published (use the same port in the dashboard)."
  fi
}

logs()   { docker logs -f "${CONTAINER}"; }
status() { docker ps --filter "name=${CONTAINER}"; }

require_docker
case "${1:-deploy}" in
  deploy) build; up ;;
  build)  build ;;
  up)     up ;;
  down)   down; log "Removed ${CONTAINER}." ;;
  logs)   logs ;;
  status) status ;;
  *) err "Unknown command '$1'."; grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac

