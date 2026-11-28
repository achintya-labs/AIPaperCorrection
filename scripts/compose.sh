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

# EXTRA_DOMAINS: run.sh writes docker-compose.domains.yml (one more login portal per extra domain); use it when it is there.
FILES=""
[ ! -f docker-compose.domains.yml ] || FILES="-f docker-compose.yml -f docker-compose.domains.yml"

if [ "${COMPOSE_RUNTIME:-podman}" = "docker" ]; then
  # shellcheck disable=SC2086
  exec docker compose -p "$PROJECT" $FILES "$@"
fi

PC="$ROOT/.venv/bin/podman-compose"
[ -x "$PC" ] || { echo "podman-compose is not installed in this repo yet. Run: ./scripts/setup-dev.sh" >&2; exit 1; }
# Which gateway profile to start: from .env (AUTH_MODE=sso|local), default sso. Use `--profile` yourself to override.
MODE=$(sed -n 's/^AUTH_MODE=//p' .env 2>/dev/null | head -1)
# shellcheck disable=SC2086
exec "$PC" -p "$PROJECT" $FILES --profile "${MODE:-sso}" "$@"
