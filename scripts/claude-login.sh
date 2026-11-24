#!/usr/bin/env sh
# Manage the Claude login held by the `claude` container (stored in the claude-home volume).
#
#   ./scripts/claude-login.sh            sign in (interactive; run once)
#   ./scripts/claude-login.sh status     show whether Claude is logged in
#   ./scripts/claude-login.sh logout     sign out
#   ./scripts/claude-login.sh token      alternative: create a long-lived token to put in .env
set -eu
cd "$(dirname "$0")/.."

# Container runtime: the repo-local podman-compose if it is installed (./scripts/setup-dev.sh), else Docker.
# Override with e.g.  COMPOSE="docker compose -p papergrader" ./scripts/claude-login.sh
if [ -z "${COMPOSE:-}" ]; then
  if [ -x ./.venv/bin/podman-compose ]; then COMPOSE=./scripts/compose.sh; else COMPOSE="docker compose -p papergrader"; fi
fi

# Only the token is needed to run one-off commands; compose requires it to be set.
[ -f .env ] || { echo "Missing .env - run: cp .env.example .env  (and set CLAUDE_RUNNER_TOKEN)"; exit 1; }

run() { $COMPOSE run --rm "$@"; }

case "${1:-login}" in
  login)
    # A release bundle (release.env) has no Dockerfiles: it runs the published image, which was already pulled.
    [ -f release.env ] || $COMPOSE build claude
    echo
    echo "Follow the prompts: open the printed URL in your browser, sign in, and paste the code back here."
    echo
    run -it claude claude auth login
    echo
    run claude claude auth status --text
    echo
    echo "Done. The login is stored in the 'claude-home' volume and survives restarts and rebuilds."
    ;;
  status) run claude claude auth status --text ;;
  logout) run claude claude auth logout ;;
  token)
    echo "This prints a long-lived token. Put it in .env as CLAUDE_CODE_OAUTH_TOKEN=..., then:"
    echo "  $COMPOSE up -d claude"
    echo
    run -it claude claude setup-token
    ;;
  *) echo "usage: $0 [login|status|logout|token]"; exit 2 ;;
esac
