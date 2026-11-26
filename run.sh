#!/usr/bin/env sh
# Paper Grader launcher. Podman + podman-compose only.
#
#   ./run.sh up        start (or recreate) everything:            podman-compose up -d --force-recreate
#   ./run.sh down      stop and remove the containers:            podman-compose down
#   ./run.sh restart   down, then up
#   ./run.sh pull      download the latest images:                podman-compose pull
#   ./run.sh pullup    down, pull, up: update to the latest release
#   ./run.sh status | logs [service] | login | env
#
# First run (all options are optional and are stored in .env, so later runs need none of them):
#   ./run.sh --host grader.example.edu --admin-email me@example.edu
#   --host H                 name or IP people will browse to (repeatable)
#   --admin-email E          first admin's email
#   --https-port P           HTTPS port (default 8443; rootless can't bind below 1024)
#
# On up/restart of a release it also asks GitHub (3 s timeout, silent on any failure) for the newest release and tells
# you if the image you have is older; update with ./run.sh pullup.
#
# What it does for you, in order:
#   1. checks that podman is installed and that your user has subuid/subgid ranges (rootless needs them);
#   2. uses podman-compose if it is already installed; otherwise looks for Python 3.14 (warns and carries on with
#      python3 if it is missing), makes ./.venv and installs podman-compose into it;
#   3. uses ./.env if it exists, otherwise creates it with fresh random secrets (and a self-signed certificate).
# Re-running never overwrites an existing .env or certificate. To start over, delete them.
#
# A release bundle has no source (no ./api), and its docker-compose.yml points at the published "latest" images, so
# they are pulled. In a git checkout the images are built locally the first time.
set -eu
cd "$(dirname "$0")"

# Baked in by the release workflow (it rewrites this line in the bundle); "dev" in a git checkout.
VERSION=dev
echo "Paper Grader $VERSION"

die() { echo "run.sh: $*" >&2; exit 1; }
warn() { echo "run.sh: warning: $*" >&2; }
rand_hex() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"
  else head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

# ---------------------------------------------------------------- options
HOSTS=""; ADMIN_EMAIL=""; HTTPS_PORT_ARG=""
CMD=""; REST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) [ $# -ge 2 ] || die "--host needs a value"; HOSTS="$HOSTS $2"; shift 2 ;;
    --admin-email) [ $# -ge 2 ] || die "--admin-email needs a value"; ADMIN_EMAIL=$2; shift 2 ;;
    --https-port) [ $# -ge 2 ] || die "--https-port needs a value"; HTTPS_PORT_ARG=$2; shift 2 ;;
    -h|--help) sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1 (see ./run.sh --help)" ;;
    *) if [ -z "$CMD" ]; then CMD=$1; else REST="$REST $1"; fi; shift ;;
  esac
done
CMD=${CMD:-up}
case "$CMD" in
  up|down|restart|pull|pullup|status|logs|login|env) ;;
  *) die "unknown command '$CMD' (up, down, restart, pull, pullup, status, logs, login, env)" ;;
esac

# ---------------------------------------------------------------- 1. podman, subuid/subgid
command -v podman >/dev/null 2>&1 || die "podman is not installed. Install it first:
    Fedora/RHEL:    sudo dnf install podman
    Debian/Ubuntu:  sudo apt install podman
    macOS:          brew install podman && podman machine init && podman machine start
    other:          https://podman.io/docs/installation"

if [ "$(uname -s)" = Linux ] && [ "$(id -u)" != 0 ]; then
  me=$(id -un); uid=$(id -u)
  for f in /etc/subuid /etc/subgid; do
    grep -Eq "^($me|$uid):[0-9]+:[0-9]+" "$f" 2>/dev/null || die "no entry for '$me' in $f. Rootless podman needs a range of subordinate ids. Fix (needs root once):
    sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $me
    podman system migrate
  then run ./run.sh again."
  done
fi

# ---------------------------------------------------------------- 2. podman-compose (PATH, else ./.venv)
PC=""
if command -v podman-compose >/dev/null 2>&1; then PC=$(command -v podman-compose)
elif [ -x ./.venv/bin/podman-compose ]; then PC=$PWD/.venv/bin/podman-compose
else
  PYBIN=""
  # `command -v` is not enough: a pyenv shim exists for versions that aren't active. The interpreter must run.
  if python3.14 -c 'import sys; sys.exit(sys.version_info[:2] != (3, 14))' >/dev/null 2>&1; then PYBIN=python3.14
  elif python3 -c 'import sys' >/dev/null 2>&1; then
    PYBIN=python3
    warn "Python 3.14 was not found; continuing with $(python3 --version 2>&1)."
  else
    die "podman-compose is not installed and there is no python3 to install it with. Install podman-compose (e.g. 'sudo dnf install podman-compose') or python3, then run ./run.sh again."
  fi
  if [ ! -x ./.venv/bin/python ]; then
    echo "Creating ./.venv with $PYBIN ..."
    "$PYBIN" -m venv .venv || die "could not create ./.venv (Debian/Ubuntu: sudo apt install python3-venv)."
  fi
  echo "Installing podman-compose into ./.venv ..."
  ./.venv/bin/pip install --quiet --disable-pip-version-check podman-compose || die "could not install podman-compose into ./.venv (no network?)"
  PC=$PWD/.venv/bin/podman-compose
fi
compose() { "$PC" -p papergrader "$@"; }

# A release bundle has no source tree: its compose file names the published "latest" images, which are pulled.
PUBLISHED=0
[ -d api ] || PUBLISHED=1

# ---------------------------------------------------------------- 3. .env and certificate
setenv() { # setenv KEY VALUE  (replace the line, or append)
  case "$2" in *'|'*|*'
'*) die "value for $1 contains a character that is not allowed" ;; esac
  if grep -q "^$1=" .env 2>/dev/null; then
    tmp=$(mktemp); sed "s|^$1=.*|$1=$2|" .env > "$tmp" && cat "$tmp" > .env; rm -f "$tmp"
  else printf '%s=%s\n' "$1" "$2" >> .env; fi
}
env_get() { sed -n "s/^$1=//p" .env 2>/dev/null | head -1; }

CREATED_ENV=0
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
  setenv HTTPS_PORT "$port"
  setenv COOKIE_SECURE true
  setenv PUBLIC_BASE_URL "https://$first:$port"
  echo "Created .env with new random secrets (mode 600)."
  echo "  First admin:  $admin_email   password: $admin_pw   <- shown once; change it after signing in"
  CREATED_ENV=1
}

make_cert() {
  if [ ! -f certs/tls.crt ] || [ ! -f certs/tls.key ]; then
    command -v openssl >/dev/null 2>&1 || die "openssl is needed once to make the self-signed certificate. Install it, or put your own certs/tls.crt and certs/tls.key in place."
    # shellcheck disable=SC2086
    ./scripts/make-cert.sh ${HOSTS:-}
  fi
}

[ -f .env ] || make_env

# ---------------------------------------------------------------- commands
need_published() { [ "$PUBLISHED" = 1 ] || die "pull needs a release bundle (this is a git checkout). In a git checkout the images are built locally."; }
# Is a newer release out? Compares the version label of the local api image with the latest GitHub release.
# Best effort: no image yet, private repo, offline, slow network: say nothing.
check_update() {
  [ "$PUBLISHED" = 1 ] || return 0
  registry=$(env_get PAPERGRADER_REGISTRY)
  [ -n "$registry" ] || registry=$(sed -n 's/.*image: \${PAPERGRADER_REGISTRY:-\([^}]*\)}\/api:.*/\1/p' docker-compose.yml | head -1)
  repo=${registry#ghcr.io/}
  [ "$repo" != "$registry" ] || return 0
  cur=$(podman image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$registry/api:$(env_get PAPERGRADER_TAG | sed 's/^$/latest/')" 2>/dev/null || true)
  case "$cur" in v[0-9]*|[0-9]*) ;; *) return 0 ;; esac
  url="https://api.github.com/repos/$repo/releases/latest"
  if command -v curl >/dev/null 2>&1; then body=$(curl -fsS --connect-timeout 2 --max-time 3 "$url" 2>/dev/null || true)
  elif command -v wget >/dev/null 2>&1; then body=$(wget -qO- -T 3 -t 1 "$url" 2>/dev/null || true)
  else return 0; fi
  latest=$(printf '%s' "$body" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
  [ -n "$latest" ] || return 0
  [ "${latest#v}" != "${cur#v}" ] || return 0
  [ "$(printf '%s\n%s\n' "${cur#v}" "${latest#v}" | sort -V | tail -1)" = "${latest#v}" ] || return 0
  echo "A new version is available: $latest (you have $cur). Update with:  ./run.sh pullup"
  echo
}
do_up() {
  check_update
  make_cert
  compose up -d --force-recreate
  echo
  echo "Running. Open  $(env_get PUBLIC_BASE_URL)   (self-signed certificate: the browser will warn once)"
  echo "Claude must be signed in once:  ./run.sh login"
  [ "$CREATED_ENV" = 1 ] || echo "Sign in with the admin account from .env (BOOTSTRAP_ADMIN_EMAIL / _PASSWORD) if no users exist yet."
}

case "$CMD" in
  up) do_up ;;
  down) compose down ;;
  restart) compose down && do_up ;;
  pull) need_published; compose pull ;;
  pullup) need_published; compose down && compose pull && do_up ;;
  status) compose ps ;;
  logs) # shellcheck disable=SC2086
        compose logs -f --tail 200 $REST ;;
  env) echo ".env is at $(pwd)/.env (mode $(stat -c %a .env 2>/dev/null || stat -f %Lp .env))"
       echo "Admin email: $(env_get BOOTSTRAP_ADMIN_EMAIL)" ;;
  login) [ -x ./scripts/claude-login.sh ] || die "scripts/claude-login.sh is missing"
         COMPOSE="$PC -p papergrader" ./scripts/claude-login.sh ;;
esac
