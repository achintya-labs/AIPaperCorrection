#!/usr/bin/env sh
# Runs the Postgres-only tests (api/tests_pg) against a throwaway Postgres container, then removes it.
# Uses podman if present, else docker. Nothing touches your real database or volumes.
set -eu
cd "$(dirname "$0")/.."
RT=$(command -v podman || command -v docker || true)
[ -n "$RT" ] || { echo "Need podman or docker to start a throwaway Postgres." >&2; exit 1; }
PY="$PWD/.venv/bin/python"
[ -x "$PY" ] || { echo "No .venv yet. Run ./scripts/setup-dev.sh first." >&2; exit 1; }

NAME=pgtest-$$
PORT=${PG_TEST_PORT:-55432}
cleanup() { "$RT" rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

"$RT" run -d --rm --name "$NAME" -e POSTGRES_PASSWORD=x -e POSTGRES_DB=t -p "$PORT:5432" \
  --tmpfs /var/lib/postgresql/data docker.io/library/postgres:16 >/dev/null
i=0; until "$RT" exec "$NAME" pg_isready -U postgres -d t >/dev/null 2>&1; do
  i=$((i+1)); [ "$i" -lt 40 ] || { echo "Postgres did not start" >&2; exit 1; }; sleep 1
done
sleep 2   # pg_isready is true slightly before the init scripts finish

cd api
PG_TEST_URL="postgresql+psycopg://postgres:x@127.0.0.1:$PORT/t" "$PY" -m pytest tests_pg "$@"
