#!/usr/bin/env sh
# One-time developer setup, everything local to this repo:
#   - .venv with the Python dev tools (pytest, podman-compose, actionlint) and the API's dependencies
#   - web/node_modules
#   - the git hook that runs the tests before every commit
set -eu
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}
[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/pip install --quiet --disable-pip-version-check -r requirements-dev.txt
echo "python tools: ok  (.venv/bin/pytest, .venv/bin/podman-compose, .venv/bin/actionlint)"

if command -v npm >/dev/null 2>&1; then
  (cd web && npm ci --no-audit --no-fund >/dev/null)
  echo "web dependencies: ok"
else
  echo "npm not found: skipped web dependencies (install Node 22 to work on the web app)"
fi

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git config core.hooksPath .githooks
  echo "git hook: ok  (tests run before every commit; see .githooks/pre-commit)"
else
  echo "not a git repository yet: run 'git init' then re-run this script to install the commit hook"
fi

cat <<'MSG'

Next:
  cp .env.example .env            # then set CLAUDE_RUNNER_TOKEN and JWT_SECRET (openssl rand -hex 32)
  ./scripts/compose.sh up --build -d
  ./scripts/claude-login.sh
  make test                       # the same checks the commit hook and CI run
MSG
