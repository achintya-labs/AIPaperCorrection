# How it runs

For the design see [architecture.md](architecture.md); for the diagrams of each flow see
[workflows.md](workflows.md).

## Prerequisites

- Docker with Compose v2, **or** Podman 5 (rootless works). `podman-compose` is installed *in the repo* by
  `./scripts/setup-dev.sh`; nothing system-wide is needed.
- About 3 GB of disk for images (the `claude` image is ~1.5 GB because it carries Node and the CLI).
- A way to authenticate Claude: an interactive login (Claude subscription), an OAuth token, or an API key.

## Start it

```sh
cp .env.example .env
# edit .env:
#   CLAUDE_RUNNER_TOKEN  openssl rand -hex 32
#   JWT_SECRET           openssl rand -hex 32
#   BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD
#   ALLOWED_HOSTS        the hostname users will browse to (see below)
./scripts/setup-dev.sh             # once: repo-local .venv (podman-compose, pytest), web deps, commit hook
./scripts/compose.sh up --build -d # podman-compose from this repo; or: docker compose -p papergrader up --build -d
./scripts/claude-login.sh          # once: sign Claude in
```

Open http://localhost:4321 and sign in as the bootstrap admin. `compose` refuses to start if
`CLAUDE_RUNNER_TOKEN` or `JWT_SECRET` is empty.

### Podman

```sh
./scripts/setup-dev.sh                                   # once; installs podman-compose into ./.venv
./scripts/compose.sh up --build -d                       # wraps .venv/bin/podman-compose -p papergrader
./scripts/claude-login.sh                                # uses the same wrapper
podman exec -it papergrader_claude_1 claude auth login   # equivalent, by hand
```

See [development.md](development.md) for the rest of the tooling (`make` targets, tests, CI).

Image names in the Dockerfiles and compose file are fully qualified (`docker.io/library/...`) because podman
refuses short names when it can't prompt. Containers are named `<project>_<service>_1`.

### First-run checklist

1. `docker compose ps`: `db` and `claude` show *healthy*; `api`, `worker`, `web` are up.
2. Exams page shows no "Grading isn't ready" banner (or `GET /grader/status` returns `"ready": true`).
3. Create a TA (Users), an exam, and a rubric.
4. Import one or two PDFs with *Preview* first, assigned to your TA.
5. The papers go `queued` → `grading` → `ready for review`; sign in as the TA and review one.

## Configuration reference

All settings are environment variables. In compose they come from `.env`, **but only the ones listed in
`docker-compose.yml` reach the containers** (`x-app-env` for api/worker, the `claude` block for the runner).
Rows marked *not wired* have a default in `api/app/config.py` but are not passed through compose; add them to
`x-app-env` (or the `claude` service) to change them.

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `CLAUDE_RUNNER_TOKEN` | **required** | api, worker, claude | Shared bearer secret between the grader client and the runner |
| `JWT_SECRET` | **required** | api | Signs session cookies. Changing it logs everyone out |
| `JWT_EXPIRE_MINUTES` | `720` | api | Session lifetime |
| `COOKIE_SECURE` | `false` | api | Set `true` behind HTTPS so the cookie gets the `Secure` flag |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `PUBLIC_BASE_URL` | empty | api | Enables [Sign in with Google](#sign-in-with-google) when all three are set |
| `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` | empty | api | First admin, created at startup **only if there are no users** |
| `DB_PASSWORD` | `grader` | db, api, worker | Postgres password (built into `DATABASE_URL`) |
| `GRADER_PROVIDER` | `claude_runner` (compose) | api, worker | `claude_runner` (CLI in the `claude` container), `claude_cli` (CLI in the same process), `fake`, `anthropic_api` (stub). The code default outside compose is `claude_cli` |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | api, worker | Model used unless the exam sets an override |
| `CLAUDE_SPLIT_MODEL` | empty (uses `CLAUDE_MODEL`) | api | Optional model used for PDF auto-split analysis (e.g. `haiku`) |
| `GRADER_TIMEOUT_SECONDS` | `600` | api, worker | Per attempt; also drives stale-job recovery |
| `WORKER_CONCURRENCY` | `2` | worker, claude | Worker grading threads; also the runner's concurrent CLI limit |
| `GRADER_MAX_ATTEMPTS` | `2` | worker | Attempts per paper for invalid model output. *not wired* |
| `RENDER_DPI` | `150` | worker | Page image resolution. *not wired* |
| `JOB_POLL_SECONDS` | `2.0` | worker | Idle poll interval. *not wired* |
| `WORKER_ID` | container hostname | worker | Identifies a worker so it can requeue its own interrupted jobs after a restart. Must be unique per running worker; containers already are. *not wired* |
| `MAX_UPLOAD_MB` | `100` | api | Per uploaded file (413 above). *not wired* |
| `CORS_ORIGINS` | `http://localhost:4321` | api | Unused in the proxy design (browser is same-origin). *not wired* |
| `STORAGE_DIR` | `/data/storage` (compose) | api, worker | Where PDFs and page PNGs live (the `storage` volume) |
| `CLAUDE_RUNNER_URL` | `http://claude:9000` (compose) | api, worker | Where the runner is |
| `CLAUDE_RUNNER_MAX_REQUEST_MB` | `300` | claude | Largest runner request. *not wired* |
| `CLAUDE_BIN` | `claude` | claude (`claude_cli` mode) | CLI executable. *not wired* |
| `CLAUDE_BARE` | `false` | claude | `--bare`: ignores the login, API key only. Leave `false` for the login/OAuth routes |
| `ANTHROPIC_API_KEY` | empty | claude only | Pay-per-use API auth instead of a login |
| `CLAUDE_CODE_OAUTH_TOKEN` | empty | claude only | Long-lived token from `claude-login.sh token` |
| `CLAUDE_CODE_ENABLE_TELEMETRY`, `OTEL_*` | off | claude only | See [Telemetry](#telemetry) |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | web (runtime) | Hostnames users browse to; see below |
| `PAPERGRADER_REGISTRY`, `PAPERGRADER_TAG` | `localhost/papergrader`, `local` | compose | Which images to use; set to a registry and version to run [published images](development.md#running-the-published-images) |
| `API_INTERNAL_URL` | `http://api:8000` (compose) | web | Where the proxy forwards `/api/*` |

### ALLOWED_HOSTS

The web service refuses cross-site writes: a request that changes data must come from a page served by the host
it was sent to, and that host must be in `ALLOWED_HOSTS` (comma-separated; `*.example.com` allows any subdomain).
If the hostname people browse to is not listed, **uploads and body-less POSTs fail with 403 "Cross-site POST form
submissions are forbidden"**. `localhost,127.0.0.1` is the default.

It is read at **runtime**, so one image (including the published one) works on any hostname. Change it in `.env`
and restart only the web container: `./scripts/compose.sh up -d --force-recreate --no-deps web`. Behind a reverse
proxy that rewrites `Host`, make sure it sends `X-Forwarded-Host` (the check prefers it).

Behind HTTPS, terminate TLS in a reverse proxy (nginx, Caddy, Traefik) that forwards `Host` and
`X-Forwarded-Proto`, and set `COOKIE_SECURE=true`. The compose file ships one: see [HTTPS](#https-reverse-proxy).

### HTTPS reverse proxy

The `proxy` service (nginx, [proxy/nginx.conf](../proxy/nginx.conf)) terminates TLS and forwards to `web`. It is the
only service published on the network: `web` is bound to `127.0.0.1:4321` and `api` to `127.0.0.1:8000`.

1. Make a certificate: `./scripts/make-cert.sh grader.school.edu 10.0.0.5` (names and/or IPs people will use;
   `localhost` and `127.0.0.1` are always included). It is **self-signed**, so browsers warn until the certificate
   is trusted; your real certificate is a drop-in replacement: put it in `certs/tls.crt` (full chain) and `certs/tls.key`, then restart `proxy`.
2. In `.env`: add the host to `ALLOWED_HOSTS`, set `COOKIE_SECURE=true`, and
   `PUBLIC_BASE_URL=https://<host>:<HTTPS_PORT>` if you use Google sign-in.
3. `./scripts/compose.sh up -d`, then open `https://<host>:8443`.

`HTTPS_PORT` defaults to `8443` because rootless Podman cannot bind ports below 1024; set `443` where allowed.
Regenerate the self-signed one with `--force` and restart `proxy`. HSTS is deliberately off while the certificate is
self-signed. Google sign-in needs a real domain name (not an IP address) in the OAuth redirect URI.

### Sign in with Google

Optional. Google only proves *who* someone is; it never creates accounts. A person can sign in with Google only if an
admin has already created a user (Admin → Users) whose email equals their verified Google email (case-insensitive) and
the user is active. Password login keeps working next to it.

1. In Google Cloud Console → APIs & Services → Credentials, create an **OAuth client ID** of type *Web application*.
2. Add the authorized redirect URI `<PUBLIC_BASE_URL>/api/auth/google/callback`
   (for local dev: `http://localhost:4321/api/auth/google/callback`).
3. Put `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `PUBLIC_BASE_URL` (what users type in the browser, no trailing
   slash) in `.env`, then restart `api`. The login page now shows a "Sign in with Google" button.

Failures send the user back to the login page with a generic message; the reason is in the `api` log
(`google sign-in rejected: …`).

## Claude login

`./scripts/claude-login.sh` runs `claude auth login` in the `claude` container: open the printed URL, sign in,
paste the code back. The credentials are stored in the `claude-home` volume, so they survive restarts and
rebuilds.

| Command | Does |
|---|---|
| `./scripts/claude-login.sh` | Interactive sign-in (once) |
| `./scripts/claude-login.sh status` | Shows whether Claude is logged in |
| `./scripts/claude-login.sh logout` | Signs out |
| `./scripts/claude-login.sh token` | Alternative: creates a long-lived token to put in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`, then `docker compose up -d claude` |

Or set `ANTHROPIC_API_KEY` in `.env` instead. The script uses the repo-local `podman-compose` when `.venv` exists
(`./scripts/setup-dev.sh`), otherwise `docker compose`; override with `COMPOSE="..."`. Or run
`podman exec -it papergrader_claude_1 claude auth login` directly.

A subscription login is intended for interactive personal use and has usage limits; check the terms before
running a whole course on one. An API key is the supported route for an automated service.

## Telemetry

Off by default. To export Claude Code's usage metrics (sessions, tokens, cost, active time) over OpenTelemetry
to your own collector, set in `.env`:

```
CLAUDE_CODE_ENABLE_TELEMETRY=1
OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector
OTEL_EXPORTER_OTLP_PROTOCOL=http/json
```

then recreate the container: `docker compose up -d --force-recreate --no-deps claude`.

- Prompt text is not exported (it contains student work). `OTEL_LOG_USER_PROMPTS` and
  `OTEL_LOG_TOOL_DETAILS` are deliberately not passed through compose.
- Account fields (email, account and organisation id) are attached by default.
- Each paper is a short `claude -p` process, so the export interval is 1 s and the shutdown flush 5 s; metrics
  from a process killed mid-run can be lost.
- Do not use the `console` exporter: it writes to stdout and corrupts the CLI's JSON output.
- Claude Code's own standard analytics is separate and on unless `DISABLE_TELEMETRY=1` is set.

## Operating it

### Logs and health

```sh
docker compose logs -f worker          # "grading submission N" / "-> ready_for_review|failed"
docker compose logs claude             # runner requests
curl -s localhost:8000/health          # api liveness (dev port)
```

`GET /grader/status` (admin) reports whether the grading backend is usable.

### Deleting papers and exams

Admins can delete both, permanently:

- **A paper** (Papers tab → **Delete**): removes its grades, page images, grading history and assignments, and the stored
  PDF. The student record stays.
- **An exam** (Exams page or the exam's page → **Delete exam**): removes all its rubric versions and every paper in it.
  If it has papers you are shown how many (and how many TA-reviewed grades they hold) and must **type the exam's name**.
- Anything with **TA-reviewed grades** asks for a second confirmation first.
- A paper that is **being graded right now cannot be deleted**; wait for it to finish (or for a stuck job to be requeued).
- There is no undo. Back up first if in doubt (see [Backups](#backups)).

### Searching tables

Every table has a search box: **My queue**, **Exams**, **Users**, each exam's **Papers** tab, and the **import preview**.
Type to filter as you go: every word must match (in any order), ignoring case and accents, so `ann bio` finds Ann's
Biology paper and `jose` finds José. Press **/** to jump to the box and **Esc** to clear it; while searching it shows
"N of M". On **My queue** it also searches the exam name and status, and combines with the Exam and Status filters.
TAs only see papers and exams assigned to them, so they only ever search within those.

### Managing users

Admins manage accounts on the **Users** page (or `PATCH`/`DELETE /auth/users/{id}`, see [api.md](api.md#auth)).

- **Change role** (TA ↔ admin) and **deactivate/reactivate** take effect immediately, even for someone signed in.
- **Deactivate** blocks sign-in and keeps their assignments and review history: the safe way to offboard a TA.
- **Delete** is permanent. Their paper assignments are removed (the papers stay, unassigned). If they have reviewed
  grades, the delete is refused with the count; you can force it, which keeps the grades but clears the reviewer.
- You can't demote, deactivate or delete **yourself**, and the last active admin is protected. To hand over, promote
  someone else first.

**Passwords.** Everyone can change their own from the **Account** page (click your email in the top bar): it needs
the current password, and signs out all your other sessions while keeping this one. Admins can reset someone else's
from the Users page (**Reset password**, with a **Generate** button for a strong random one shown once). A reset signs
that person out everywhere. Rules everywhere: 8 or more characters, at most 72 bytes. An admin can't reset their *own*
password this way; they use the Account page like anyone else. There is no email-based "forgot password": the admin
resets it, and if the *only* admin forgets theirs, run this in the `api` container:

```sh
docker compose exec api python -c "from app.db import SessionLocal; from app.models import User; from app.auth import hash_password; \
s=SessionLocal(); u=s.query(User).filter_by(email='YOU@example.com').one(); u.password_hash=hash_password('a-new-password'); u.token_version+=1; s.commit()"
```

### Scaling and throughput

- `WORKER_CONCURRENCY` threads per worker container, and the runner allows the same number of concurrent CLI
  calls. Raise both together via that one variable.
- More workers: `docker compose up -d --scale worker=3`. `SKIP LOCKED` keeps them from claiming the same paper.
- Rate limits and cost scale with concurrency; start low.
- A paper takes roughly 10 s for a few pages on a small model, longer for real handwriting on larger models.

### Backups

```sh
docker compose exec db pg_dump -U grader grader > backup.sql          # all grades, rubrics, users
docker run --rm -v papergrader_storage:/data -v "$PWD":/out docker.io/library/alpine \
  tar czf /out/storage.tgz -C /data .                                 # PDFs and page images
```

(The volume name is `<project>_storage`; run `docker volume ls` to confirm.) The Claude login is easiest to
recreate with `claude-login.sh` than to back up.

### Upgrades

```sh
git pull && docker compose up --build -d
```

The `api` container runs `alembic upgrade head` on start. Rubric versions and AI/TA grade columns are kept.

### Reset

`docker compose down` stops the stack and keeps data. `docker compose down -v` also **deletes** the database,
the stored scans **and the Claude login**.

## Restarts, reboots and durability

**Your data survives restarts.** Everything that matters lives in three *named volumes*, not inside containers:

| Volume | Holds | Survives |
|---|---|---|
| `dbdata` | Postgres: users, exams, rubrics, grades, review history | `restart`, `stop`/`start`, `down`, container recreation, rebuilds, upgrades |
| `storage` | Uploaded PDFs and page images | same |
| `claude-home` | The Claude login | same |

They are **deleted** by `docker compose down -v`, and also by `docker volume prune` / `system prune --volumes` while the
stack is *down* (no container is using them then). Don't run those without a backup. See [Backups](#backups).

### What happens on a restart

[workflows.md section 8](workflows.md#8-restarts-and-recovery) has the flowchart. In short:

- **Every service has `restart: unless-stopped`**, so a crashed container, or all of them after the container engine
  restarts, come back on their own. Only an explicit `stop` keeps one down.
- **Postgres** is stopped with `SIGINT` (fast shutdown) so a normal stop is clean and quick. After an *unclean* death
  (power loss, OOM kill) it replays its write-ahead log on the next start and loses no committed data.
- **The API waits for the database.** On start it retries its migration for about a minute, so starting everything at
  once after a reboot is fine. A real migration error still stops it, with the message in `docker compose logs api`.
- **`web` waits for a healthy API**, and runs under an init process so it stops promptly on `SIGTERM`.
- **Papers being graded when the stack stopped are graded again.** A restarted worker requeues the jobs it had claimed
  (identified by its container hostname) immediately; any other orphaned job is requeued by the periodic sweep after
  `GRADER_TIMEOUT_SECONDS × GRADER_MAX_ATTEMPTS + 120 s`. Results are saved in a single transaction at the end of a
  grade, so nothing is ever half-written. The interrupted model call is paid for twice.
- **Claude stays logged in**, because the login is in `claude-home`.

What *is* lost: a grade edit typed in a browser but not yet saved, an upload that was in flight, and
OpenTelemetry metrics not yet flushed.

Durability is not a backup: a failed disk or a deleted volume still loses everything. Back up regularly.

### Verified, and not

Tested on the real stack (podman 5.8, Postgres 16) with live data:

- Recreating every container on new images, including applying a new migration to a populated database: identical row
  counts and user fingerprint, all stored files present, a marker file in `claude-home` intact.
- Clean stop of the whole stack: Postgres logged a *fast shutdown* and stopped in under a second; the next start needed
  no crash recovery. `web` stops in 0.1 s (it previously took the full grace period and was killed).
- `SIGKILL` of Postgres in the middle of a stream of committed inserts: it ran crash recovery on restart and every
  acknowledged row was present, with no gaps.
- A crashed `api` and `db` process (killed from the host) were restarted by their restart policy, `api` healthy within
  about 12 s.
- The API's wait-for-database loop: with no database it retried and stayed up, then migrated and became healthy once
  Postgres appeared.
- A restarted worker requeued only its own interrupted job and left another worker's untouched.

**Not tested:** an actual host reboot, a Docker daemon restart (Docker, as opposed to podman), and running out of disk.

### Docker

The daemon starts containers with a restart policy by itself when it starts, so nothing else is needed.

### Podman (rootless)

Restart policies recover **crashes**, but after a **host reboot** rootless containers only return if your user systemd
instance is running and the boot service is enabled. Check, and enable if needed:

```sh
loginctl show-user "$USER" -p Linger        # Linger=yes is required
systemctl --user is-enabled podman-restart.service
loginctl enable-linger "$USER"
systemctl --user enable --now podman-restart.service
```

Other podman-compose notes: it starts a container's dependencies when you `podman start` it; it does not apply
`stop_grace_period` (containers keep podman's 10 s default, which is enough for everything here); and a container
stopped with `podman kill` or `podman stop` is deliberately *not* restarted by its policy (only crashes are).

## Local development without containers

```sh
cd api && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export DATABASE_URL=sqlite:///dev.db STORAGE_DIR=./storage GRADER_PROVIDER=fake JWT_SECRET=dev \
       BOOTSTRAP_ADMIN_EMAIL=admin@example.com BOOTSTRAP_ADMIN_PASSWORD=devpassword
alembic upgrade head
uvicorn app.main:app --port 8000 &
python -m app.jobs.worker &
cd ../web && npm ci && API_INTERNAL_URL=http://127.0.0.1:8000 npm run dev
```

`GRADER_PROVIDER=fake` grades deterministically (half marks) without calling Claude; use `claude_cli` to run the
CLI on the host with your own login. SQLite is fine for development; it does not exercise `FOR UPDATE SKIP LOCKED`.

## Tests

```sh
make test        # API (119 tests, ~8 s), web unit tests, type check, openapi.json check
make test-pg     # Postgres-only tests against a throwaway container
```

More in [development.md](development.md): the commit hook that requires these to pass, CI, and publishing images.

The API tests run on SQLite and use a stand-in `claude` executable, so they never call the real model. The
end-to-end checks used during development (full stack through the proxy, and a real browser) are not part of
the repository yet; see [libraries.md](libraries.md#testing).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Papers `failed`: "Claude is not logged in…" | Run `claude-login.sh`, then *Queue waiting & failed* on the exam's Papers tab |
| Banner "Grading isn't ready" on Exams page | Same: not logged in, or `CLAUDE_RUNNER_URL/TOKEN` wrong; the banner shows the reason |
| "Claude runner rejected CLAUDE_RUNNER_TOKEN" | Token differs between `.env` and the running `claude` container. Recreate it: `up -d --force-recreate claude` |
| "Claude runner unreachable" | `claude` container is down or unhealthy: `docker compose logs claude` |
| Paper stays `grading` | Worker died mid-job. It is requeued automatically after `GRADER_TIMEOUT_SECONDS × GRADER_MAX_ATTEMPTS + 120 s`; restart the worker if it is not running |
| 403 "Cross-site POST form submissions are forbidden" | Host not in `ALLOWED_HOSTS` (edit `.env`, recreate `web`), or your own client is not sending an `Origin` header |
| 413 on upload | File over `MAX_UPLOAD_MB`, or a reverse proxy body-size limit |
| Everyone logged out suddenly | `JWT_SECRET` changed |
| One person keeps getting signed out | Their password was changed or reset (this ends their other sessions), or they were deactivated or deleted |
| "Current password is incorrect" on the Account page | Expected on a wrong guess; you stay signed in. If you've forgotten it, ask an admin to reset it |
| Compose: "set CLAUDE_RUNNER_TOKEN in .env" | A required variable is empty |
| Interactive login hangs or fails in the container | Use `claude-login.sh token` or an API key |
| Containers don't come back after a host reboot (podman) | Rootless podman needs lingering and `podman-restart.service`; see [Podman (rootless)](#podman-rootless) |
| `api` log shows "waiting for the database (n/30)" | Normal during start-up; it retries for about a minute. If it ends with "database never became ready", check `docker compose logs db` |
| Can't delete a user: "has reviewed N grade(s)" | Deactivate them instead, or delete with `force=true` to clear the reviewer on those grades |
| "You can't demote or deactivate yourself" / "At least one active admin is required" | Have another admin make the change, or promote someone first |
| Telemetry not arriving | Endpoint unreachable from the container, `CLAUDE_CODE_ENABLE_TELEMETRY` not `1`, or the process was killed before flushing |
| Podman: "short-name resolution" error | Use fully qualified image names (already done in this repo) |
