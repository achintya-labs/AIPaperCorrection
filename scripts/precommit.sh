#!/usr/bin/env sh
# The checks a commit must pass. Run by .githooks/pre-commit and CI-equivalent, and usable by hand.
# Only the parts that your staged changes can affect are run, so small commits stay quick.
#
#   ./scripts/precommit.sh          decide from `git diff --cached`
#   ./scripts/precommit.sh --all    run everything
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "No .venv yet. Run ./scripts/setup-dev.sh first." >&2; exit 1; }

if [ "${1:-}" = "--all" ]; then
  # pretend one file in each area changed
  CHANGED=$(printf '%s\n' api/app/main.py docs/api.md web/src/middleware.ts .github/workflows/ci.yml)
else
  CHANGED=$(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null || true)
fi
touches() { printf '%s\n' "$CHANGED" | grep -Eq "$1"; }

ran=0
if touches '^(api/|docs/|scripts/export-openapi\.py|requirements-dev\.txt|\.githooks/)'; then
  echo "==> API tests (pytest)"
  (cd api && "$PY" -m pytest)
  echo "==> docs/openapi.json is current"
  "$PY" scripts/export-openapi.py --check
  ran=1
fi
if touches '^web/'; then
  echo "==> web unit tests"
  (cd web && npm test --silent)
  echo "==> web type check"
  (cd web && npx --no-install tsc --noEmit)
  ran=1
fi
if touches '^\.github/workflows/'; then
  echo "==> workflow lint (actionlint)"
  "$ROOT/.venv/bin/actionlint"
  ran=1
fi
[ "$ran" = 1 ] || echo "No code, docs or workflow changes staged: nothing to test."
echo "All checks passed."
