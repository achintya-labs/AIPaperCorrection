#!/usr/bin/env sh
# Paper Grader launcher. Two ways to run, same commands:
#
#   containers (default)  Docker or rootless Podman. A release bundle pulls the published images; a git checkout builds them.
#   --native              NO containers, no root: Python + Node on this machine, SQLite for the database (or --db postgres).
#
#   ./run.sh --host grader.example.edu --admin-email me@example.edu        first run (containers): .env, certificate, start
#   ./run.sh --native --host grader.example.edu --admin-email me@example.edu   the same without containers
#   ./run.sh status | logs [service] | stop | down | env | login | pull (containers only)
#
# First-run options (stored in .env, so later runs need none of them):
#   --host H                 name or IP people will browse to (repeatable)
#   --admin-email E          first admin's email
#   --https-port P           HTTPS port (default 8443; rootless can't bind below 1024)
# Native-only options:
#   --db sqlite|postgres     database (default sqlite: one file in ./data, no server). postgres needs --db-url
#   --db-url URL             e.g. postgresql+psycopg://user:pass@host:5432/dbname
#   --no-https / --https     plain HTTP on --port (default 4321) / HTTPS through proxy/tls-proxy.mjs (default)
#   --listen ADDR            address to listen on (default 0.0.0.0 for HTTPS, 127.0.0.1 for plain HTTP)
#   --port N                 plain-HTTP port (default 4321)
#   --no-claude              don't install or use the Claude CLI; the fake grader is used (to try the UI)
#
# Re-running never overwrites an existing .env or certificate. To start over, delete them (native data: ./data).
set -eu
cd "$(dirname "$0")"

die() { echo "run.sh: $*" >&2; exit 1; }
rand_hex() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"
  else head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}

# ---------------------------------------------------------------- options
HOSTS=""; ADMIN_EMAIL=""; HTTPS_PORT_ARG=""
NATIVE=""; DB_ARG=""; DB_URL_ARG=""; HTTPS_ARG=""; LISTEN_ARG=""; PORT_ARG=""; NO_CLAUDE=""
CMD=""; REST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) [ $# -ge 2 ] || die "--host needs a value"; HOSTS="$HOSTS $2"; shift 2 ;;
    --admin-email) [ $# -ge 2 ] || die "--admin-email needs a value"; ADMIN_EMAIL=$2; shift 2 ;;
    --https-port) [ $# -ge 2 ] || die "--https-port needs a value"; HTTPS_PORT_ARG=$2; shift 2 ;;
    --native) NATIVE=1; shift ;;
    --db) [ $# -ge 2 ] || die "--db needs sqlite or postgres"; DB_ARG=$2; shift 2 ;;
    --db-url) [ $# -ge 2 ] || die "--db-url needs a value"; DB_URL_ARG=$2; shift 2 ;;
    --no-https) HTTPS_ARG=0; shift ;;
    --https) HTTPS_ARG=1; shift ;;
    --listen) [ $# -ge 2 ] || die "--listen needs an address"; LISTEN_ARG=$2; shift 2 ;;
    --port) [ $# -ge 2 ] || die "--port needs a number"; PORT_ARG=$2; shift 2 ;;
    --no-claude) NO_CLAUDE=1; shift ;;
    -h|--help) sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option $1 (see ./run.sh --help)" ;;
    *) if [ -z "$CMD" ]; then CMD=$1; else REST="$REST $1"; fi; shift ;;
  esac
done
CMD=${CMD:-up}

# A native bundle has no compose file: that is native mode.
if [ -z "$NATIVE" ] && [ ! -f docker-compose.yml ] && [ -d api ]; then NATIVE=1; fi
NATIVE=${NATIVE:-0}

if [ "$NATIVE" = 0 ]; then
  [ -z "$DB_ARG$DB_URL_ARG$HTTPS_ARG$LISTEN_ARG$PORT_ARG$NO_CLAUDE" ] || \
    die "--db, --db-url, --https, --no-https, --listen, --port and --no-claude only apply with --native"
fi
case "${DB_ARG:-sqlite}" in sqlite|postgres) ;; *) die "--db must be sqlite or postgres" ;; esac
[ "${DB_ARG:-}" = postgres ] && [ -z "$DB_URL_ARG" ] && ! grep -q '^DATABASE_URL=' .env 2>/dev/null && \
  die "--db postgres needs --db-url postgresql+psycopg://user:pass@host:5432/dbname"

# ---------------------------------------------------------------- .env helpers
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
  if [ "$NATIVE" = 1 ]; then
    https=${HTTPS_ARG:-1}
    setenv NATIVE_HTTPS "$https"
    if [ "$https" = 1 ]; then
      setenv COOKIE_SECURE true; setenv PUBLIC_BASE_URL "https://$first:$port"
    else
      setenv COOKIE_SECURE false; setenv PUBLIC_BASE_URL "http://$first:${PORT_ARG:-4321}"
    fi
    setenv NATIVE_DB "${DB_ARG:-sqlite}"
    [ -n "$DB_URL_ARG" ] && setenv DATABASE_URL "$DB_URL_ARG"
    [ -n "$NO_CLAUDE" ] && setenv GRADER_PROVIDER fake
  else
    setenv COOKIE_SECURE true
    setenv PUBLIC_BASE_URL "https://$first:$port"
    if [ "$PUBLISHED" = 1 ]; then setenv PAPERGRADER_REGISTRY "$PAPERGRADER_REGISTRY"; setenv PAPERGRADER_TAG "$PAPERGRADER_TAG"; fi
  fi
  echo "Created .env with new random secrets (mode 600)."
  echo "  First admin:  $admin_email   password: $admin_pw   <- shown once; change it after signing in"
  CREATED_ENV=1
}

make_cert() {
  if [ ! -f certs/tls.crt ] || [ ! -f certs/tls.key ]; then
    command -v openssl >/dev/null 2>&1 || die "openssl is needed once to make the self-signed certificate. Install it, put your own certs/tls.crt and certs/tls.key in place, or use --no-https (native)."
    # shellcheck disable=SC2086
    ./scripts/make-cert.sh ${HOSTS:-}
  fi
}

# ================================================================ native mode (no containers)
native_main() {
  RUN_DIR=./native
  mkdir -p "$RUN_DIR/pids" "$RUN_DIR/logs"
  PY=./.venv/bin/python

  # An explicit flag changes a stored choice; otherwise .env decides.
  case "$CMD" in up|start)
    [ -f .env ] || make_env
    [ -n "$DB_ARG" ] && setenv NATIVE_DB "$DB_ARG"
    [ -n "$DB_URL_ARG" ] && setenv DATABASE_URL "$DB_URL_ARG"
    [ -n "$HTTPS_ARG" ] && setenv NATIVE_HTTPS "$HTTPS_ARG"
    [ -n "$NO_CLAUDE" ] && setenv GRADER_PROVIDER fake
    [ -n "$PORT_ARG" ] && setenv NATIVE_PORT "$PORT_ARG"
    [ -n "$HTTPS_PORT_ARG" ] && setenv HTTPS_PORT "$HTTPS_PORT_ARG"
    if [ -n "$HTTPS_ARG$PORT_ARG$HTTPS_PORT_ARG" ] && [ "$CREATED_ENV" = 0 ]; then
      # An explicit scheme or port flag: rebuild the public URL (used for Google sign-in and the message below).
      pub_host=$(env_get PUBLIC_BASE_URL | sed -E 's#^https?://##; s#:[0-9]+$##'); pub_host=${pub_host:-localhost}
      if [ "$(env_get NATIVE_HTTPS)" = 0 ]; then setenv PUBLIC_BASE_URL "http://$pub_host:$(env_get NATIVE_PORT | sed 's/^$/4321/')"
      else setenv PUBLIC_BASE_URL "https://$pub_host:$(env_get HTTPS_PORT | sed 's/^$/8443/')"; fi
    fi
    true ;;
  esac
  [ -f .env ] || die "no .env yet: run ./run.sh --native first"

  # Export .env for the child processes (KEY=VALUE lines only; no shell evaluation).
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; *=*) k=${line%%=*}; v=${line#*=}; case "$k" in *[!A-Za-z0-9_]*) continue ;; esac; export "$k=$v" ;; esac
  done < .env

  DB_KIND=${NATIVE_DB:-sqlite}
  USE_HTTPS=${NATIVE_HTTPS:-1}
  WEB_PORT=${PORT_ARG:-${NATIVE_PORT:-4321}}
  API_PORT=${API_PORT:-8000}
  HTTPS_PORT=${HTTPS_PORT:-8443}
  mkdir -p data/storage
  if [ "$DB_KIND" = sqlite ]; then
    export DATABASE_URL="${DATABASE_URL:-sqlite:///$PWD/data/papergrader.db}"
    case "$DATABASE_URL" in sqlite*) ;; *) die ".env has NATIVE_DB=sqlite but DATABASE_URL=$DATABASE_URL" ;; esac
  else
    [ -n "${DATABASE_URL:-}" ] || die "NATIVE_DB=postgres needs DATABASE_URL in .env (or --db-url)"
  fi
  export STORAGE_DIR="${STORAGE_DIR:-$PWD/data/storage}"
  export GRADER_PROVIDER="${GRADER_PROVIDER:-claude_cli}"
  [ "$GRADER_PROVIDER" = fake ] || export GRADER_PROVIDER=claude_cli
  export API_INTERNAL_URL="http://127.0.0.1:$API_PORT"
  [ "$USE_HTTPS" = 1 ] && export COOKIE_SECURE=true  # over HTTPS the session cookie must be Secure
  if [ "$USE_HTTPS" = 1 ]; then LISTEN=${LISTEN_ARG:-0.0.0.0}; WEB_HOST=127.0.0.1; else LISTEN=${LISTEN_ARG:-127.0.0.1}; WEB_HOST=$LISTEN; fi

  pidfile() { echo "$RUN_DIR/pids/$1.pid"; }
  is_running() { [ -f "$(pidfile "$1")" ] && kill -0 "$(cat "$(pidfile "$1")")" 2>/dev/null; }
  start_proc() { # start_proc NAME DIR cmd...   (detached, logs to native/logs/NAME.log)
    name=$1; dir=$2; shift 2
    if is_running "$name"; then echo "  $name: already running (pid $(cat "$(pidfile "$name")"))"; return; fi
    ( cd "$dir" && exec nohup "$@" ) </dev/null >> "$RUN_DIR/logs/$name.log" 2>&1 &
    echo $! > "$(pidfile "$name")"
    echo "  $name: started (pid $!)"
  }
  stop_proc() {
    name=$1
    is_running "$name" || { rm -f "$(pidfile "$name")"; return; }
    pid=$(cat "$(pidfile "$name")"); kill "$pid" 2>/dev/null || true
    i=0; while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 20 ]; do i=$((i+1)); sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    rm -f "$(pidfile "$name")"; echo "  $name: stopped"
  }

  port_free() { "$PY" -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", int(sys.argv[1])))' "$1" 2>/dev/null; }
  pick_port() { p=$1; n=0; while ! port_free "$p"; do p=$((p+1)); n=$((n+1)); [ "$n" -lt 50 ] || die "no free port found near $1"; done; echo "$p"; }
  # Ports: keep the ones a running process already uses; otherwise use the wanted port, or the next free one.
  choose_ports() {
    if is_running api && [ -f "$RUN_DIR/api.port" ]; then API_PORT=$(cat "$RUN_DIR/api.port")
    elif ! port_free "$API_PORT"; then want=$API_PORT; API_PORT=$(pick_port "$((API_PORT+1))"); echo "  API port $want is busy, using $API_PORT"; fi
    echo "$API_PORT" > "$RUN_DIR/api.port"
    if is_running web && [ -f "$RUN_DIR/web.port" ]; then WEB_PORT=$(cat "$RUN_DIR/web.port")
    elif ! port_free "$WEB_PORT"; then
      if [ -n "$PORT_ARG${NATIVE_PORT:-}" ]; then die "port $WEB_PORT is busy (choose another with --port)"; fi
      want=$WEB_PORT; WEB_PORT=$(pick_port "$((WEB_PORT+1))"); echo "  Web port $want is busy, using $WEB_PORT"
    fi
    echo "$WEB_PORT" > "$RUN_DIR/web.port"
    if [ "$USE_HTTPS" = 1 ] && ! is_running proxy && ! port_free "$HTTPS_PORT"; then
      die "HTTPS port $HTTPS_PORT is busy: pick another with --https-port N (it is remembered in .env)"
    fi
    export API_INTERNAL_URL="http://127.0.0.1:$API_PORT"
  }

  resolve_claude() { # sets CLAUDE_BIN (installing the CLI into ./tools if needed)
    if [ -n "${CLAUDE_BIN:-}" ] && [ -x "$CLAUDE_BIN" ]; then return; fi
    if command -v claude >/dev/null 2>&1; then CLAUDE_BIN=$(command -v claude)
    elif [ -x ./tools/node_modules/.bin/claude ]; then CLAUDE_BIN=$PWD/tools/node_modules/.bin/claude
    else
      command -v npm >/dev/null 2>&1 || die "npm is needed once to install the Claude Code CLI (npm install --prefix ./tools @anthropic-ai/claude-code), or use --no-claude to try the UI without it."
      echo "Installing the Claude Code CLI into ./tools (no root needed) ..."
      npm install --prefix ./tools --no-audit --no-fund @anthropic-ai/claude-code >/dev/null
      CLAUDE_BIN=$PWD/tools/node_modules/.bin/claude
    fi
    export CLAUDE_BIN
  }

  case "$CMD" in
    up|start)
      # --- Python
      PYBIN=${PYTHON:-python3}
      command -v "$PYBIN" >/dev/null 2>&1 || die "python3 (3.10 or newer) is needed. Install it, or set PYTHON=/path/to/python3."
      "$PYBIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10 or newer is needed (found $("$PYBIN" --version 2>&1))."
      if [ ! -x "$PY" ]; then
        echo "Creating ./.venv ..."
        "$PYBIN" -m venv .venv || die "could not create a virtual environment (on Debian/Ubuntu without root: use a Python from pyenv/conda, or 'pip install --user virtualenv')."
      fi
      want=$(cksum < api/requirements.txt | awk '{print $1}')
      if [ "$(cat .venv/.req-cksum 2>/dev/null || true)" != "$want" ]; then
        echo "Installing Python dependencies into ./.venv ..."
        .venv/bin/pip install --quiet --disable-pip-version-check -r api/requirements.txt
        echo "$want" > .venv/.req-cksum
      fi
      # --- Node
      command -v node >/dev/null 2>&1 || die "Node.js 20 or newer is needed for the web server (a tarball from nodejs.org works without root)."
      if [ ! -f web/dist/server/entry.mjs ]; then
        command -v npm >/dev/null 2>&1 || die "web/dist is missing and npm is not available to build it."
        echo "Building the web app ..."
        ( cd web && ASTRO_TELEMETRY_DISABLED=1 npm ci --no-audit --no-fund >/dev/null && ASTRO_TELEMETRY_DISABLED=1 npm run build >/dev/null )
      elif [ ! -d web/node_modules ]; then
        command -v npm >/dev/null 2>&1 || die "npm is needed once to install the web server's dependencies."
        echo "Installing the web server's dependencies ..."
        ( cd web && npm ci --omit=dev --no-audit --no-fund >/dev/null )
      fi
      [ "$GRADER_PROVIDER" = fake ] && echo "Using the fake grader (no Claude): papers get placeholder grades." || resolve_claude
      [ "$USE_HTTPS" = 1 ] && make_cert

      # --- migrate, then start
      echo "Database: $DB_KIND ($( [ "$DB_KIND" = sqlite ] && echo "$DATABASE_URL" | sed 's|^sqlite:///||' || echo "external" ))"
      ( cd api && "../$PY" -m alembic upgrade head ) >> "$RUN_DIR/logs/migrate.log" 2>&1 || { tail -n 15 "$RUN_DIR/logs/migrate.log" >&2; die "database migration failed (full log: native/logs/migrate.log)"; }
      echo "Starting:"
      choose_ports
      start_proc api api "../$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT"
      start_proc worker api "../$PY" -m app.jobs.worker
      start_proc web web env HOST="$WEB_HOST" PORT="$WEB_PORT" NODE_ENV=production node ./dist/server/entry.mjs
      if [ "$USE_HTTPS" = 1 ]; then
        start_proc proxy . env TLS_CERT="$PWD/certs/tls.crt" TLS_KEY="$PWD/certs/tls.key" LISTEN_HOST="$LISTEN" \
          LISTEN_PORT="$HTTPS_PORT" UPSTREAM_PORT="$WEB_PORT" node proxy/tls-proxy.mjs
      fi
      # --- wait for the API
      i=0; until "$PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$API_PORT/health', timeout=2)" >/dev/null 2>&1; do
        i=$((i+1))
        { is_running api && [ "$i" -lt 60 ]; } || { echo "The API did not come up; last log lines:" >&2; tail -n 15 "$RUN_DIR/logs/api.log" >&2; exit 1; }
        sleep 1
      done
      echo
      if [ "$USE_HTTPS" = 1 ]; then echo "Running. Open  $(env_get PUBLIC_BASE_URL)   (self-signed certificate: the browser will warn once)"
      else
        echo "Running. Open  $(env_get PUBLIC_BASE_URL | sed -E 's#:[0-9]+$##'):$WEB_PORT   (plain HTTP on $LISTEN:$WEB_PORT)"
        [ "$(env_get COOKIE_SECURE)" = true ] && echo "Note: .env has COOKIE_SECURE=true; over plain HTTP sign-in only works behind your own TLS proxy. Set COOKIE_SECURE=false otherwise."
      fi
      [ "$GRADER_PROVIDER" = fake ] || echo "Claude must be signed in once:  ./run.sh --native login"
      [ "$CREATED_ENV" = 1 ] || echo "Sign in with the admin account from .env (BOOTSTRAP_ADMIN_EMAIL / _PASSWORD) if no users exist yet."
      echo "Stop with ./run.sh --native stop.   Data: ./data   Logs: ./native/logs"
      ;;
    stop|down)
      for n in proxy web worker api; do stop_proc "$n"; done ;;
    status|ps)
      for n in api worker web proxy; do
        if is_running "$n"; then echo "$n: running (pid $(cat "$(pidfile "$n")"))"; else echo "$n: stopped"; fi
      done
      echo "database: $DB_KIND   grader: $GRADER_PROVIDER   url: $(env_get PUBLIC_BASE_URL)   api port: $(cat "$RUN_DIR/api.port" 2>/dev/null || echo -)" ;;
    logs)
      set -- ; for n in $REST; do set -- "$@" "$RUN_DIR/logs/$n.log"; done
      [ $# -gt 0 ] || set -- "$RUN_DIR"/logs/api.log "$RUN_DIR"/logs/worker.log "$RUN_DIR"/logs/web.log
      tail -n 100 -f "$@" ;;
    env) echo ".env is at $(pwd)/.env (mode $(stat -c %a .env 2>/dev/null || stat -f %Lp .env))"
         echo "Admin email: $(env_get BOOTSTRAP_ADMIN_EMAIL)   database: $DB_KIND" ;;
    login) [ "$GRADER_PROVIDER" = fake ] && die "this install uses the fake grader (--no-claude); there is no Claude to sign in"
           resolve_claude; "$CLAUDE_BIN" auth login ;;
    pull) die "pull only applies to the container mode" ;;
    *) die "unknown command '$CMD' (up, stop, status, logs, env, login)" ;;
  esac
}

if [ "$NATIVE" = 1 ]; then native_main; exit 0; fi

# ================================================================ container mode
find_compose() {
  [ -n "${COMPOSE:-}" ] && { echo "$COMPOSE"; return; }
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; return; fi
  if command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then echo "podman compose"; return; fi
  if command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; return; fi
  if command -v podman-compose >/dev/null 2>&1; then echo "podman-compose"; return; fi
  if [ -x ./.venv/bin/podman-compose ]; then echo "./.venv/bin/podman-compose"; return; fi
  return 1
}
COMPOSE_CMD=$(find_compose) || die "no container runtime found. Install Docker or Podman (rootless Podman needs no root once set up: https://podman.io/docs/installation), or run without containers: ./run.sh --native"
compose() { $COMPOSE_CMD -p papergrader "$@"; }

# A bundle carries release.env, so use the published images; a checkout builds locally.
PUBLISHED=0
if [ -f release.env ]; then
  PUBLISHED=1
  # shellcheck disable=SC1091
  . ./release.env
  export PAPERGRADER_REGISTRY PAPERGRADER_TAG
fi

case "$CMD" in
  up|start)
    [ -f .env ] || make_env
    make_cert ;;
esac

[ -f .env ] || die "no .env yet: run ./run.sh first"

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
        compose logs -f --tail 200 $REST ;;
  env) echo ".env is at $(pwd)/.env (mode $(stat -c %a .env 2>/dev/null || stat -f %Lp .env))"; echo "Admin email: $(env_get BOOTSTRAP_ADMIN_EMAIL)" ;;
  login) [ -x ./scripts/claude-login.sh ] || die "scripts/claude-login.sh is missing"
         COMPOSE="$COMPOSE_CMD -p papergrader" ./scripts/claude-login.sh ;;
  *) die "unknown command '$CMD' (up, stop, down, pull, status, logs, env, login)" ;;
esac
