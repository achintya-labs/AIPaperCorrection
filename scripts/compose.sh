#!/usr/bin/env sh
# Run compose for this project using the podman-compose installed in THIS repo's .venv:
# no Docker, no system-wide install, nothing in /tmp.
#
#   ./scripts/compose.sh up -d              start (or update) the stack
#   ./scripts/compose.sh build api web      rebuild images
#   ./scripts/compose.sh logs -f worker
#
# COMPOSE_RUNTIME=docker ./scripts/compose.sh ...   use `docker compose` instead.
# The project name is fixed to "papergrader" so containers and volumes are the same whichever way you start it.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PROJECT=${COMPOSE_PROJECT_NAME:-papergrader}

if [ "${COMPOSE_RUNTIME:-podman}" = "docker" ]; then
  exec docker compose -p "$PROJECT" "$@"
fi

PC="$ROOT/.venv/bin/podman-compose"
[ -x "$PC" ] || { echo "podman-compose is not installed in this repo yet. Run: ./scripts/setup-dev.sh" >&2; exit 1; }
exec "$PC" -p "$PROJECT" "$@"
