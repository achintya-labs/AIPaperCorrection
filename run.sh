#!/usr/bin/env sh
# Paper Grader launcher. Works from a release bundle (pulls the published images) or from a git checkout
# (builds the images locally). Needs only a container runtime: Docker or Podman (rootless is fine).
#
#   ./run.sh                          first run: writes .env with fresh secrets and a self-signed certificate, starts everything
#   ./run.sh --host grader.example.edu --host 10.0.0.5 --admin-email me@example.edu
#                                     first-run options: names/IPs people will browse to, and the first admin's email
#   ./run.sh status | logs [service] | stop | down | pull | env | login
#
# Re-running never overwrites an existing .env or certificate. To start over, delete them.
set -eu
cd "$(dirname "$0")"

die() { echo "run.sh: $*" >&2; exit 1; }
rand_hex() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"
  else head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

# ---- options (only used when .env is first created) ----
HOSTS=""; ADMIN_EMAIL=""; HTTPS_PORT_ARG=""
CMD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) [ $# -ge 2 ] || die "--host needs a value"; HOSTS="$HOSTS $2"; shift 2 ;;
    --admin-email) [ $# -ge 2 ] || die "--admin-email needs a value"; ADMIN_EMAIL=$2; shift 2 ;;
    --https-port) [ $# -ge 2 ] || die "--https-port needs a value"; HTTPS_PORT_ARG=$2; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1 (see ./run.sh --help)" ;;
    *) if [ -z "$CMD" ]; then CMD=$1; else REST="${REST:-} $1"; fi; shift ;;
  esac
done
CMD=${CMD:-up}

# ---- container runtime ----
find_compose() {
  [ -n "${COMPOSE:-}" ] && { echo "$COMPOSE"; return; }
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; return; fi
  if command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then echo "podman compose"; return; fi
  if command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; return; fi
  if command -v podman-compose >/dev/null 2>&1; then echo "podman-compose"; return; fi
  if [ -x ./.venv/bin/podman-compose ]; then echo "./.venv/bin/podman-compose"; return; fi
  return 1
}
COMPOSE_CMD=$(find_compose) || die "no container runtime found. Install Docker or Podman (rootless Podman needs no root once set up: https://podman.io/docs/installation), or: python3 -m venv .venv && .venv/bin/pip install podman-compose (still needs podman)."
compose() { $COMPOSE_CMD -p papergrader "$@"; }

# ---- release pin: a bundle carries release.env, so use the published images; a checkout builds locally ----
PUBLISHED=0
if [ -f release.env ]; then
  PUBLISHED=1
  # shellcheck disable=SC1091
  . ./release.env
  export PAPERGRADER_REGISTRY PAPERGRADER_TAG
fi

# ---- .env ----
setenv() { # setenv KEY VALUE  (replace the line, or append)
  case "$2" in *'|'*|*'
'*) die "value for $1 contains a character that is not allowed" ;; esac
  if grep -q "^$1=" .env 2>/dev/null; then
    tmp=$(mktemp); sed "s|^$1=.*|$1=$2|" .env > "$tmp" && cat "$tmp" > .env; rm -f "$tmp"
  else printf '%s=%s\n' "$1" "$2" >> .env; fi
}

make_env() {
  [ -f .env.example ] || die ".env.example is missing next to run.sh"
  : "${HOSTS:=localhost 127.0.0.1}"
  first=$(printf '%s' "$HOSTS" | awk '{print $1}')
  port=${HTTPS_PORT_ARG:-8443}
  admin_email=${ADMIN_EMAIL:-admin@example.com}
  admin_pw=$(rand_hex 9)
  umask 077
  cp .env.example .env
  chmod 600 .env
  setenv JWT_SECRET "$(rand_hex 32)"
  setenv CLAUDE_RUNNER_TOKEN "$(rand_hex 32)"
  setenv DB_PASSWORD "$(rand_hex 16)"
  setenv BOOTSTRAP_ADMIN_EMAIL "$admin_email"
  setenv BOOTSTRAP_ADMIN_PASSWORD "$admin_pw"
  setenv ALLOWED_HOSTS "localhost,127.0.0.1$(for h in $HOSTS; do case $h in localhost|127.0.0.1) ;; *) printf ',%s' "$h" ;; esac; done)"
  setenv COOKIE_SECURE true
  setenv HTTPS_PORT "$port"
  setenv PUBLIC_BASE_URL "https://$first:$port"
  [ "$PUBLISHED" = 1 ] && { setenv PAPERGRADER_REGISTRY "$PAPERGRADER_REGISTRY"; setenv PAPERGRADER_TAG "$PAPERGRADER_TAG"; }
  echo "Created .env with new random secrets (mode 600)."
  echo "  First admin:  $admin_email   password: $admin_pw   <- shown once; change it after signing in"
  CREATED_ENV=1
}

CREATED_ENV=0
case "$CMD" in
  up|start)
    if [ ! -f .env ]; then make_env; fi
    if [ ! -f certs/tls.crt ] || [ ! -f certs/tls.key ]; then
      command -v openssl >/dev/null 2>&1 || die "openssl is needed once to make the self-signed certificate. Install it, or put your own certs/tls.crt and certs/tls.key in place."
      # shellcheck disable=SC2086
      ./scripts/make-cert.sh ${HOSTS:-}
    fi ;;
esac

[ -f .env ] || die "no .env yet: run ./run.sh first"
env_get() { sed -n "s/^$1=//p" .env | head -1; }

case "$CMD" in
  up|start)
    if [ "$PUBLISHED" = 1 ]; then
      echo "Pulling images $PAPERGRADER_REGISTRY/{api,web,claude}:$PAPERGRADER_TAG ..."
      compose pull
      compose up -d --no-build
    else
      echo "No release.env: building the images from this checkout (the first time takes a few minutes) ..."
      compose up -d --build
    fi
    port=$(env_get HTTPS_PORT); port=${port:-8443}
    echo
    echo "Running. Open  $(env_get PUBLIC_BASE_URL)   (self-signed certificate: the browser will warn once)"
    echo "Claude must be signed in once:  ./run.sh login"
    [ "$CREATED_ENV" = 1 ] || echo "Sign in with the admin account from .env (BOOTSTRAP_ADMIN_EMAIL / _PASSWORD) if no users exist yet."
    ;;
  stop) compose stop ;;
  down) compose down ;;
  pull) [ "$PUBLISHED" = 1 ] || die "pull only applies to a release bundle (no release.env here)"; compose pull ;;
  status|ps) compose ps ;;
  logs) # shellcheck disable=SC2086
        compose logs -f --tail 200 ${REST:-} ;;
  env) echo ".env is at $(pwd)/.env (mode $(stat -c %a .env 2>/dev/null || stat -f %Lp .env))"; echo "Admin email: $(env_get BOOTSTRAP_ADMIN_EMAIL)" ;;
  login) [ -x ./scripts/claude-login.sh ] || die "scripts/claude-login.sh is missing"
         COMPOSE="$COMPOSE_CMD -p papergrader" ./scripts/claude-login.sh ;;
  *) die "unknown command '$CMD' (up, stop, down, pull, status, logs, env, login)" ;;
esac
