#!/usr/bin/env sh
# Paper Grader launcher. Podman + podman-compose only.
#
#   ./run.sh up        start (or recreate) everything:            podman-compose up -d --force-recreate
#   ./run.sh down      stop and remove the containers:            podman-compose down
#   ./run.sh restart   down, then up
#   ./run.sh pull      download the latest images:                podman-compose pull
#   ./run.sh pullup    down, pull, up: update to the latest release
#   ./run.sh status | logs [service] | login | env
#   ./run.sh compose ARGS...   run podman-compose for this project with the right profile (exec, ps, build ...): the release bundle
#                              has no scripts/compose.sh, so this is the way to e.g. ./run.sh compose exec -T db pg_dump -U grader grader
#   ./run.sh caddy FILE        put a new Caddyfile into the running gateway and reload it (--show prints the live one, --revert undoes)
#   ./run.sh migrate   sso, MANUAL and never part of `up`: copy the app's existing users into LLDAP (no passwords).
#                            export-users does just the export (writes ldap-bootstrap/users.jsonl).
#
# Sign-in (AUTH_MODE in .env, default sso):
#   sso    a gateway (Caddy + Coraza WAF + Tinyauth, users in LLDAP on Postgres) signs people in and is the ONLY thing
#          published on the network: https://<DOMAIN>:<HTTPS_PORT> (default port 5904). Roles come from LDAP groups.
#   local  the app's own password / Google sign-in behind the nginx HTTPS proxy (development, break-glass).
#
# First run (all options are optional and are stored in .env, so later runs need none of them):
#   ./run.sh --domain lan.internal --admin-email me@example.edu                  (sso)
#   ./run.sh --auth-mode local --host grader.example.edu --admin-email me@...    (local)
#   --domain D               sso: base domain; the app is at D itself, the login portal auth.D, user admin lldap.D
#   --auth-mode M            sso (default) or local
#   --host H                 local: name or IP people will browse to (repeatable)
#   --admin-email E          first admin's email
#   --https-port P           HTTPS port (sso default 5904, local default 8443; rootless can't bind below 1024)
#
# On up/restart of a release it also asks GitHub (3 s timeout, silent on any failure) for the newest release and tells
# you if the image you have is older; update with ./run.sh pullup.
#
# What it does for you, in order:
#   1. checks that podman is installed and that your user has subuid/subgid ranges (rootless needs them);
#   2. uses podman-compose if it is already installed; otherwise looks for Python 3.14 (warns and carries on with
#      python3 if it is missing), makes ./.venv and installs podman-compose into it;
#   3. uses ./.env if it exists, otherwise creates it with fresh random secrets (local mode also a self-signed certificate);
#   Moving the users of an older install into LDAP is never automatic: run ./run.sh migrate yourself (docs/how-it-runs.md).
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
HOSTS=""; ADMIN_EMAIL=""; HTTPS_PORT_ARG=""; DOMAIN_ARG=""; AUTHMODE_ARG=""
CMD=""; REST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) [ $# -ge 2 ] || die "--host needs a value"; HOSTS="$HOSTS $2"; shift 2 ;;
    --admin-email) [ $# -ge 2 ] || die "--admin-email needs a value"; ADMIN_EMAIL=$2; shift 2 ;;
    --https-port) [ $# -ge 2 ] || die "--https-port needs a value"; HTTPS_PORT_ARG=$2; shift 2 ;;
    --domain) [ $# -ge 2 ] || die "--domain needs a value"; DOMAIN_ARG=$2; shift 2 ;;
    --auth-mode) [ $# -ge 2 ] || die "--auth-mode needs sso or local"; AUTHMODE_ARG=$2; shift 2 ;;
    -h|--help) sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1 (see ./run.sh --help)" ;;
    *) if [ -z "$CMD" ]; then
         CMD=$1; shift
         case "$CMD" in compose|caddy) break ;; esac   # everything after these belongs to them, flags included: "$@" keeps it intact
       else REST="$REST $1"; shift; fi ;;
  esac
done
CMD=${CMD:-up}
case "$CMD" in
  up|down|restart|pull|pullup|status|logs|login|env|export-users|migrate|compose|caddy) ;;
  *) die "unknown command '$CMD' (up, down, restart, pull, pullup, status, logs, login, env, export-users, migrate, compose, caddy)" ;;
esac
case "$AUTHMODE_ARG" in ""|sso|local) ;; *) die "--auth-mode must be sso or local" ;; esac

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
AUTH_MODE=sso  # set for real once .env exists (below)
compose() { "$PC" -p papergrader --profile "$AUTH_MODE" "$@"; }
compose_setup() { "$PC" -p papergrader --profile sso --profile setup "$@"; }  # the one-shot setup containers (run --rm)

# A release bundle has no source tree: its compose file names the published "latest" images, which are pulled.
PUBLISHED=0
[ -d api ] || PUBLISHED=1

# ---------------------------------------------------------------- 3. .env and certificate
setenv() { # setenv KEY VALUE  (replace the line, or append)
  case "$2" in *'|'*|*'
'*) die "value for $1 contains a character that is not allowed" ;; esac
  if grep -q "^$1=" .env 2>/dev/null; then
    tmp=$(mktemp); sed "s|^$1=.*|$1=$2|" .env > "$tmp" && cat "$tmp" > .env; rm -f "$tmp"
  else
    # An .env whose last line has no newline (older copies of .env.example) would get the new key glued onto it.
    [ ! -s .env ] || [ -z "$(tail -c1 .env)" ] || echo >> .env
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
env_get() { sed -n "s/^$1=//p" .env 2>/dev/null | head -1; }

setenv_missing() { [ -n "$(env_get "$1")" ] || setenv "$1" "$2"; }

# The values sso mode needs. On a new .env everything is generated; on an existing one only what is missing is added,
# so an install made before sso existed upgrades in place (its secrets, users and data are untouched).
ensure_sso_env() { # ensure_sso_env DOMAIN PORT
  domain=$1; port=$2
  setenv DOMAIN "$domain"
  setenv_missing LLDAP_JWT_SECRET "$(rand_hex 32)"
  setenv_missing LLDAP_KEY_SEED "$(rand_hex 32)"
  setenv_missing LLDAP_DB_PASSWORD "$(rand_hex 16)"
  setenv_missing LDAP_OBSERVER_PASSWORD "$(rand_hex 16)"
  if [ -z "$(env_get LLDAP_ADMIN_PASSWORD)" ]; then
    setenv LLDAP_ADMIN_PASSWORD "$(rand_hex 9)"; NEW_LLDAP_ADMIN=1
  fi
  d=$(env_get DOMAIN)
  setenv HTTPS_PORT "$port"
  setenv COOKIE_SECURE true
  setenv PUBLIC_BASE_URL "https://$d:$port"
  setenv_missing ALLOWED_HOSTS "localhost,127.0.0.1"
  case ",$(env_get ALLOWED_HOSTS)," in *",$d,"*) ;; *) setenv ALLOWED_HOSTS "$(env_get ALLOWED_HOSTS),$d" ;; esac
  setenv LOGOUT_URL "https://auth.$d:$port/logout"
  setenv LDAP_ADMIN_URL "https://lldap.$d:$port"
}

CREATED_ENV=0; NEW_LLDAP_ADMIN=0
make_env() {
  [ -f .env.example ] || die ".env.example is missing next to run.sh"
  mode=${AUTHMODE_ARG:-sso}
  admin_email=${ADMIN_EMAIL:-admin@example.com}
  umask 077
  cp .env.example .env
  chmod 600 .env
  setenv AUTH_MODE "$mode"
  setenv JWT_SECRET "$(rand_hex 32)"
  setenv CLAUDE_RUNNER_TOKEN "$(rand_hex 32)"
  setenv DB_PASSWORD "$(rand_hex 16)"
  setenv BOOTSTRAP_ADMIN_EMAIL "$admin_email"
  echo "Created .env with new random secrets (mode 600)."
  if [ "$mode" = sso ]; then
    setenv BOOTSTRAP_ADMIN_PASSWORD ""
    ensure_sso_env "${DOMAIN_ARG:-lan.internal}" "${HTTPS_PORT_ARG:-5904}"
    echo "  First admin:  user 'admin' ($admin_email)   password: $(env_get LLDAP_ADMIN_PASSWORD)   <- also in .env as LLDAP_ADMIN_PASSWORD"
  else
    : "${HOSTS:=localhost 127.0.0.1}"
    first=$(printf '%s' "$HOSTS" | awk '{print $1}')
    port=${HTTPS_PORT_ARG:-8443}
    admin_pw=$(rand_hex 9)
    setenv BOOTSTRAP_ADMIN_PASSWORD "$admin_pw"
    setenv ALLOWED_HOSTS "localhost,127.0.0.1$(for h in $HOSTS; do case $h in localhost|127.0.0.1) ;; *) printf ',%s' "$h" ;; esac; done)"
    setenv HTTPS_PORT "$port"
    setenv COOKIE_SECURE true
    setenv PUBLIC_BASE_URL "https://$first:$port"
    echo "  First admin:  $admin_email   password: $admin_pw   <- shown once; change it after signing in"
  fi
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

# Which sign-in mode runs. An .env from before sso existed has no AUTH_MODE: it is upgraded to sso (the default) in place.
if [ -n "$AUTHMODE_ARG" ] && [ "$(env_get AUTH_MODE)" != "$AUTHMODE_ARG" ] && [ "$CREATED_ENV" = 0 ]; then
  setenv AUTH_MODE "$AUTHMODE_ARG"; echo "AUTH_MODE is now $AUTHMODE_ARG."
elif [ -z "$(env_get AUTH_MODE)" ]; then
  setenv AUTH_MODE sso; echo "No AUTH_MODE in .env: switching this install to single sign-on (set AUTH_MODE=local to keep passwords)."
fi
AUTH_MODE=$(env_get AUTH_MODE)
case "$AUTH_MODE" in sso|local) ;; *) die "AUTH_MODE in .env must be sso or local (found '$AUTH_MODE')" ;; esac
if [ "$AUTH_MODE" = sso ] && [ "$CREATED_ENV" = 0 ] && { [ -z "$(env_get LLDAP_JWT_SECRET)" ] || [ -n "$DOMAIN_ARG$HTTPS_PORT_ARG" ]; }; then
  # Upgrading an older install (no gateway secrets yet) or an explicit --domain/--https-port: (re)write the sso settings.
  if [ -z "$(env_get LLDAP_JWT_SECRET)" ]; then d=${DOMAIN_ARG:-lan.internal}; p=${HTTPS_PORT_ARG:-5904}
  else d=${DOMAIN_ARG:-$(env_get DOMAIN)}; p=${HTTPS_PORT_ARG:-$(env_get HTTPS_PORT)}; fi
  ensure_sso_env "$d" "$p"
  [ "$NEW_LLDAP_ADMIN" = 0 ] || echo "  LDAP admin:  user 'admin'   password: $(env_get LLDAP_ADMIN_PASSWORD)   <- also in .env as LLDAP_ADMIN_PASSWORD"
fi

# The app used to be served at app.<DOMAIN>; it now lives at <DOMAIN> itself. Bring an older sso .env along (idempotent).
if [ "$AUTH_MODE" = sso ] && [ "$CREATED_ENV" = 0 ] && [ -n "$(env_get DOMAIN)" ]; then
  d=$(env_get DOMAIN); p=$(env_get HTTPS_PORT)
  case ",$(env_get ALLOWED_HOSTS)," in *",$d,"*) ;; *) setenv ALLOWED_HOSTS "$(env_get ALLOWED_HOSTS),$d" ;; esac
  [ "$(env_get PUBLIC_BASE_URL)" != "https://app.$d:$p" ] || setenv PUBLIC_BASE_URL "https://$d:$p"
fi

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
# MANUAL (./run.sh export-users, or migrate which also imports): print the existing users as LLDAP bootstrap documents into
# ldap-bootstrap/users.jsonl. Nothing here runs on `up`, so a reset never re-triggers a migration. The file is written HERE, on
# the host, from the container's stdout: a container can't write into a bind-mounted host folder under rootless Podman (the api
# runs as uid 1000, the folder belongs to another uid). Users keep their papers and grades: the app matches them by email on
# their first sign-in.
export_users() {
  echo "Exporting the app's existing users ..."
  mkdir -p ldap-bootstrap && chmod 755 ldap-bootstrap
  compose up -d db >/dev/null 2>&1 || { warn "could not start the database to export users"; return 1; }
  i=0; until compose exec -T db pg_isready -U grader >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -lt 30 ] || { warn "the database is not ready"; return 1; }; sleep 1
  done
  out=$(mktemp)
  if compose run --rm -T --no-deps api python -m app.export_ldap_users > "$out"; then
    grep '^{' "$out" > ldap-bootstrap/users.jsonl || true  # keep only the JSON lines; an empty file means "nobody to export"
    chmod 644 ldap-bootstrap/users.jsonl
    echo "  $(wc -l < ldap-bootstrap/users.jsonl | tr -d ' ') user(s) written to ldap-bootstrap/users.jsonl"
  else
    warn "user export failed (an api image older than this run.sh also causes this: git checkout -> ./run.sh up rebuilds, release bundle -> ./run.sh pullup)"
    rm -f "$out"
    return 1
  fi
  rm -f "$out"
}

# MANUAL: export the existing users, then import them into LLDAP (without passwords, in the group matching their old role). Needs
# the sso stack up so LLDAP is running; people then get a password in the LLDAP UI (or use Google sign-in). Safe to repeat.
migrate_users() {
  [ "$AUTH_MODE" = sso ] || die "migrate is for AUTH_MODE=sso"
  export_users || die "export failed; nothing was imported"
  [ -s ldap-bootstrap/users.jsonl ] || { echo "No active users to migrate."; return 0; }
  echo "Importing them into LLDAP ..."
  compose up -d lldap >/dev/null 2>&1 || die "could not start LLDAP (is the stack up? try ./run.sh up first)"
  i=0; until compose exec -T lldap /app/lldap healthcheck --config-file /data/lldap_config.toml >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -lt 60 ] || die "LLDAP did not become ready"; sleep 1
  done
  "$PC" -p papergrader --profile sso --profile migrate run --rm -T --no-deps lldap-import || die "the LLDAP import failed (output above)"
  echo
  echo "Done. Set each person's password in LLDAP (https://lldap.$(env_get DOMAIN):$(env_get HTTPS_PORT), user 'admin'): Users, the person,"
  echo "Modify password. Or use Google sign-in (OAUTH_WHITELIST). On their first sign-in they keep their papers and grades."
}

# In a git checkout `up` alone would reuse whatever image was built last time (podman-compose only builds a MISSING image),
# so new code (and the user export below, which runs inside the api image) would never take effect. Layer caching makes
# this fast when nothing changed. A release bundle pulls published images instead (./run.sh pullup updates them).
build_images() {
  [ "$PUBLISHED" = 0 ] || return 0
  echo "Building images (cached layers are reused; the first time takes a few minutes) ..."
  compose build || die "image build failed (see the output above)"
}

# sso: a SELF-SIGNED certificate for <DOMAIN>, auth.<DOMAIN> and lldap.<DOMAIN>, handed to Caddy through gateway/tls/tls.caddy. (Caddy's own CA
# would need its root trusted in every browser, and a browser that has met another "Caddy Local Authority" before rejects the
# new one with "certificate has an invalid signature".) To use a real certificate, replace the three files in gateway/tls/ and
# delete gateway/tls/.domain so it is not regenerated. Regenerated automatically when DOMAIN changes.
make_gateway_cert() {
  d=$(env_get DOMAIN)
  mkdir -p gateway/tls
  if [ -f gateway/tls/wildcard.crt ] && [ -f gateway/tls/wildcard.key ] && [ -f gateway/tls/tls.caddy ] \
     && { [ ! -f gateway/tls/.domain ] || [ "$(cat gateway/tls/.domain)" = "$d" ]; }; then return 0; fi
  command -v openssl >/dev/null 2>&1 || die "openssl is needed once to make the self-signed certificate. Install it, or put your own certificate in gateway/tls/ (wildcard.crt, wildcard.key, tls.caddy)."
  ( umask 077
    openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 365 \
      -keyout gateway/tls/wildcard.key -out gateway/tls/wildcard.crt \
      -subj "/CN=$d" -addext "subjectAltName=DNS:$d,DNS:auth.$d,DNS:lldap.$d,DNS:*.$d" 2>/dev/null ) \
    || die "could not create the self-signed certificate for $d"
  chmod 644 gateway/tls/wildcard.crt
  chmod 600 gateway/tls/wildcard.key  # Caddy runs as the container's root, which is this user under rootless Podman
  printf 'tls /etc/caddy/tls/wildcard.crt /etc/caddy/tls/wildcard.key\n' > gateway/tls/tls.caddy
  printf '%s\n' "$d" > gateway/tls/.domain
  echo "Wrote a self-signed certificate for $d, auth.$d and lldap.$d (gateway/tls/, valid 365 days)."
}

# sso: the one-shot setup (create the lldap database, then the read-only account, groups and first admin), done BEFORE the long-running
# services start and run with --rm so nothing is left over for them to depend on. A service that depends on a finished one-shot
# can't be restarted by Podman after a reboot ("dependencies ... are not running: container state improper").
sso_setup() {
  podman rm -f papergrader_lldap-db-init_1 papergrader_lldap-bootstrap_1 >/dev/null 2>&1 || true  # leftovers from older versions
  echo "Preparing the user directory ..."
  compose up -d db >/dev/null 2>&1 || die "could not start the database"
  i=0; until compose exec -T db pg_isready -U grader >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -lt 60 ] || die "the database did not become ready"; sleep 1
  done
  compose_setup run --rm -T --no-deps lldap-db-init || die "creating the lldap database failed (output above)"
  compose up -d lldap >/dev/null 2>&1 || die "could not start LLDAP"
  i=0; until compose exec -T lldap /app/lldap healthcheck --config-file /data/lldap_config.toml >/dev/null 2>&1; do
    i=$((i+1)); [ "$i" -lt 90 ] || die "LLDAP did not become ready (./run.sh logs lldap)"; sleep 1
  done
  log=$(mktemp)
  if ! compose_setup run --rm -T --no-deps lldap-bootstrap > "$log" 2>&1; then
    tail -n 25 "$log" >&2
    rm -f "$log"
    die "the LLDAP bootstrap failed (output above)"
  fi
  rm -f "$log"
}

# Stop and remove the containers. `compose down` complains ("no container with name or ID ...") for every service of the profile
# it is given that was never created, so it only gets the running mode's profile; containers that belong to no running profile
# (the other mode's, and the one-shot setup/migrate ones) are removed here, only when they actually exist.
do_down() {
  if [ "$AUTH_MODE" = sso ]; then others="proxy"; else others="caddy tinyauth lldap"; fi
  for svc in $others lldap-db-init lldap-bootstrap lldap-import; do
    if podman container exists "papergrader_${svc}_1" 2>/dev/null; then
      podman rm -f "papergrader_${svc}_1" >/dev/null 2>&1 || warn "could not remove papergrader_${svc}_1"
    fi
  done
  compose down
}

# MANUAL: put a new Caddyfile into the RUNNING gateway container and reload Caddy (no downtime, no rebuild). This is for a server that
# only has the release bundle (the Caddyfile is baked into the image there). The file is validated inside the container first, so it sees
# the container's real DOMAIN, LLDAP_ADMIN_USERS and certificates; a failed reload restores the previous file. The change lives in the
# container only: up / pullup recreate it from the image. To keep it, change gateway/Caddyfile in the repo and release.
caddy_update() {
  [ "$AUTH_MODE" = sso ] || die "caddy is for AUTH_MODE=sso (there is no gateway in local mode)"
  conf=/etc/caddy/Caddyfile
  compose exec -T caddy true >/dev/null 2>&1 || die "the caddy container is not running (./run.sh status). Start the stack first: ./run.sh up"
  case "${1:-}" in
    --show) compose exec -T caddy cat "$conf"; return ;;
    --revert)
      compose exec -T caddy sh -c "test -f $conf.bak" || die "there is no previous Caddyfile to go back to"
      compose exec -T caddy sh -c "cp $conf.bak $conf"
      compose exec -T caddy caddy reload --config "$conf" --adapter caddyfile || die "reloading the previous Caddyfile failed (see above)"
      echo "Reverted to the previous Caddyfile."; return ;;
    ""|-h|--help) echo "usage: ./run.sh caddy FILE | --show | --revert"; return ;;
  esac
  [ -s "$1" ] || die "'$1' does not exist or is empty"
  echo "Uploading $1 ..."
  compose exec -T caddy sh -c 'cat > /tmp/Caddyfile.new' < "$1" || die "could not copy the file into the container"
  echo "Validating inside the container ..."
  if ! cout=$(compose exec -T caddy caddy validate --config /tmp/Caddyfile.new --adapter caddyfile 2>&1); then
    printf '%s\n' "$cout" | grep -v '^{"level":"info"' || true   # Caddy's info lines are noise; its error line is JSON too, so keep it
    die "the new Caddyfile is not valid; nothing was changed"
  fi
  printf '%s\n' "$cout" | grep -v '^{"level":"info"' || true
  echo "Installing and reloading ..."
  compose exec -T caddy sh -c "cp $conf $conf.bak && cp /tmp/Caddyfile.new $conf"
  if ! compose exec -T caddy caddy reload --config "$conf" --adapter caddyfile; then
    compose exec -T caddy sh -c "cp $conf.bak $conf"
    die "reload failed; the previous Caddyfile was restored"
  fi
  echo "Done. Caddy runs the new Caddyfile until the container is recreated (./run.sh up / pullup). Undo with: ./run.sh caddy --revert"
}

do_up() {
  check_update
  build_images
  if [ "$AUTH_MODE" = sso ]; then
    mkdir -p gateway/tls  # bind-mount source must exist
    make_gateway_cert
    sso_setup
  else
    make_cert
  fi
  compose up -d --force-recreate
  echo
  if [ "$AUTH_MODE" = sso ]; then
    d=$(env_get DOMAIN); p=$(env_get HTTPS_PORT)
    echo "Running. Open  https://$d:$p   (self-signed certificate: the browser warns once for each of $d, auth.$d and lldap.$d;"
    echo "accept it on auth.$d too, because signing in redirects there)"
    echo "On every machine that browses to it, /etc/hosts needs:  <this machine's IP>  $d auth.$d lldap.$d"
    echo "People are managed in LLDAP at https://lldap.$d:$p (user 'admin', password LLDAP_ADMIN_PASSWORD in .env);"
    ag=$(env_get LDAP_ADMIN_GROUP); tg=$(env_get LDAP_TA_GROUP)
    echo "put them in the group ${ag:-papergrader_admins} (admins) or ${tg:-papergrader_tas} (TAs) to give them access."
  else
    echo "Running. Open  $(env_get PUBLIC_BASE_URL)   (self-signed certificate: the browser will warn once)"
  fi
  echo "Claude must be signed in once:  ./run.sh login"
}

case "$CMD" in
  up) do_up ;;
  down) do_down ;;
  restart) do_down && do_up ;;
  pull) need_published; compose pull ;;
  pullup) need_published; do_down && compose pull && do_up ;;
  export-users) [ "$AUTH_MODE" = sso ] || die "export-users is for AUTH_MODE=sso"; export_users ;;
  migrate) migrate_users ;;
  compose) [ $# -gt 0 ] || die "usage: ./run.sh compose <podman-compose arguments>, e.g. ./run.sh compose exec -T db pg_dump -U grader grader"
           compose "$@" ;;
  caddy) caddy_update "$@" ;;
  status) compose ps ;;
  logs) # shellcheck disable=SC2086
        compose logs -f --tail 200 $REST ;;
  env) echo ".env is at $(pwd)/.env (mode $(stat -c %a .env 2>/dev/null || stat -f %Lp .env))"
       echo "Sign-in mode: $AUTH_MODE   Admin email: $(env_get BOOTSTRAP_ADMIN_EMAIL)" ;;
  login) [ -x ./scripts/claude-login.sh ] || die "scripts/claude-login.sh is missing"
         COMPOSE="$PC -p papergrader --profile $AUTH_MODE" ./scripts/claude-login.sh ;;
esac
