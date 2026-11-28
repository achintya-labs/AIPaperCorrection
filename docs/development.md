# Development, CI and releases

Everything a developer needs lives **inside the repo**: a `.venv` with the Python tools (including
`podman-compose`), `web/node_modules`, scripts, a commit hook and CI workflows. Nothing depends on a system-wide
install or on files outside the checkout.

## One-time setup

```sh
./scripts/setup-dev.sh        # same as: make setup
```

It creates `.venv` (pytest, podman-compose, actionlint and the API's dependencies from `requirements-dev.txt`),
runs `npm ci` in `web/`, and installs the commit hook (`git config core.hooksPath .githooks`). Re-run it any time
the requirements change; it is idempotent. Python 3.12 is what the images use; newer versions work for development.

| File | Contents |
|---|---|
| `api/requirements.txt` | **Runtime** dependencies only. This is what the API and worker images install |
| `claude/requirements.txt` | Runtime dependencies of the Claude runner image |
| `requirements-dev.txt` | Everything for development: includes `api/requirements.txt`, plus pytest, podman-compose, actionlint |
| `web/package.json`, `package-lock.json` | Web dependencies; the lockfile is used by `npm ci` locally, in CI and in the image |

## Everyday commands

| Command | Does |
|---|---|
| `make test` | The checks the commit hook and CI run: API tests, web unit tests, type check, `openapi.json` check |
| `make test-api` / `make test-web` | One half only |
| `make test-pg` | The Postgres-only tests against a throwaway container (podman or docker) |
| `make up` / `make down` / `make restart` | `./run.sh up/down/restart`: start, stop and remove containers, restart them (volumes and data are kept); also `make pull`, `pullup`, `migrate`, `logs`, `ps`, all via `./run.sh` |
| `make rebuild` | Rebuild the `api`, `worker`, `web` and `claude` images and recreate those containers |
| `make logs` / `make ps` | Follow logs, list containers |
| `make ci-lint` | Lint the GitHub workflows offline |

### Running the stack

`./scripts/compose.sh <args>` runs compose with the **podman-compose in this repo's `.venv`**, with the project name
fixed to `papergrader` so containers and volumes are the same whichever way you start the stack:

```sh
./scripts/compose.sh up --build -d     # passes --profile from AUTH_MODE in .env (sso by default)
./scripts/compose.sh logs -f worker
COMPOSE_RUNTIME=docker ./scripts/compose.sh up -d     # the same, with `docker compose`
```

`./scripts/claude-login.sh` uses this wrapper automatically when `.venv` exists.

## Tests

| Suite | Where | Runs on | Time |
|---|---|---|---|
| API (auth, import, grading, review, deletion, runner, docs drift) | `api/tests` | SQLite, a stand-in `claude` executable; `AUTH_MODE=local` (set in `tests/conftest.py`) | ~8 s |
| Single sign-on (`tests/test_sso.py`: `Remote-Email` -> LDAP -> cache row, roles from groups, not enrolled, 409s, directory listing, import of never-signed-in TAs, the LDAP client against an in-memory server, the user export) | `api/tests` | a fake directory and ldap3's mock server | ~1 s |
| Web unit tests (table search, cross-site request check, import button state, login errors) | `web/src/lib/*.test.ts` | Node's built-in runner | <1 s |
| Type check and build | `web/` | `astro sync` (generates the `astro:*` types), `tsc`, `astro build` | ~10 s |
| SQLite engine used by the tests (`tests/test_sqlite_mode.py`: migrations on a file, pragmas, racing claims) | `api/tests` | a temporary SQLite file | ~2 s |
| Postgres-only (foreign keys, row locks, job claiming, migrations) | `api/tests_pg` | a real Postgres; skipped unless `PG_TEST_URL` is set | ~2 s |

The API suite is fast because the tests hash passwords at bcrypt's minimum cost (`BCRYPT_ROUNDS=4`, set in
`api/tests/conftest.py`); production keeps the default of 12. The tests run on in-memory SQLite, which does not enforce foreign keys and ignores
`FOR UPDATE`, which is why `tests_pg` exists. Browser end-to-end tests are not in the repo yet (see
[libraries.md](libraries.md#testing-and-quality)).

## The commit gate

`.githooks/pre-commit` runs `scripts/precommit.sh`, which looks at what is **staged** and runs only what it can affect:

| Staged paths | Checks |
|---|---|
| `api/`, `docs/`, `scripts/export-openapi.py`, `requirements-dev.txt` | API tests, then `openapi.json` is current |
| `web/` | Web unit tests, then type check |
| `.github/workflows/` | `actionlint` |
| anything else (e.g. `README.md`) | nothing to test |

A failing check makes the hook exit non-zero and git refuses the commit. `./scripts/precommit.sh --all` runs everything.
`git commit --no-verify` skips the hook, but CI runs the same checks, so it only postpones the failure. To make CI
binding, protect `main` on GitHub and require these status checks: **API tests**, **Postgres tests and migrations**,
**Web**, **Compose and workflows**.

The Postgres suite includes a migration round trip that works for whatever the newest migration is: it undoes one
step, restores the head and runs `alembic check`. Adding a migration needs no test change.

**Sample data.** `samples/` is a small generated exam (question paper, rubric, four handwritten-style answer sheets, the
CSVs for each import mode, expected marks in its README). Regenerate with `.venv/bin/python scripts/make-sample-data.py`.

**Gotcha: `.gitignore` and `storage/`.** The runtime-data rules are anchored (`/storage/`, `/api/storage/`) because an
unanchored `storage/` also hides the `api/app/storage` Python package, which passes locally but fails CI on a fresh clone.

## CI

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`:

| Job | Does |
|---|---|
| **API tests** | `pytest` on Python 3.12, and `docs/openapi.json` matches the code |
| **Postgres tests and migrations** | Postgres 16 service: `alembic upgrade head` on an empty database, `alembic check`, then `tests_pg` |
| **Web** | `npm ci`, unit tests, `astro sync`, type check, build |
| **Compose and workflows** | `docker compose config` parses with both profiles (`sso`, `local`) and with published images; the sso profile publishes **exactly one** host port; `run.sh` and `scripts/*.sh` parse; `actionlint` on the workflows |
| **Image builds** (pull requests only) | Builds the `api`, `web`, `claude` and `gateway` Dockerfiles without pushing, so a broken Dockerfile fails before merge |

`scripts/waf-probe.py` sends realistic API requests (every endpoint: JSON edits with PUT/PATCH/DELETE, rubrics and feedback full of code, 14 to 100 MB multipart uploads, long ID lists, 1800 random session cookies) through the real `(waf)` snippet of `gateway/Caddyfile` in a throwaway container and lists any that are blocked, with the rule id; it also checks that XSS/SQLi in a URL, attack-like names and error text in an HTML response still are. Run it after changing the Caddyfile or the API. It needs podman and the built gateway image.

Not covered by CI: the gateway itself (Caddy/Coraza rules, Tinyauth, LLDAP, the self-signed certificate) needs real containers. After changing
`gateway/Caddyfile`, `docker-compose.yml` or `run.sh`, start the stack (`./run.sh up`) and walk through the checks in
[how-it-runs.md](how-it-runs.md#first-run-checklist); the sign-in flow is diagrammed in [workflows.md](workflows.md#9-signing-in-with-the-gateway-sso).

### Compose profiles and the two sign-in modes

`docker-compose.yml` puts services behind profiles: `sso` (`caddy`, `tinyauth`, `lldap`), `local` (the nginx
`proxy`), `setup` (the one-shots `lldap-db-init` and `lldap-bootstrap`, run with `--rm` by `./run.sh up` *before* the sso services) and `migrate` (`lldap-import`, run only by `./run.sh migrate`).
Long-running services must never `depends_on` a one-shot: Podman records that as `--requires`, and after a reboot it then refuses to restart the service (`dependencies … are not running: container state improper`). `run.sh` and `scripts/compose.sh` add `--profile $AUTH_MODE`; a plain
`podman-compose` call without a profile starts only `db`, `claude`, `api`, `worker` and `web`. For development without the gateway use
`AUTH_MODE=local` (`./run.sh --auth-mode local`, or the uvicorn recipe in [how-it-runs.md](how-it-runs.md#local-development-without-containers)).

## Publishing images

`.github/workflows/release.yml` runs the whole CI first (`uses: ./.github/workflows/ci.yml`) and **only then** builds
and pushes `linux/amd64` images to GitHub Container Registry (**amd64 only**: the images do not run on arm64 hosts such as Raspberry Pi or Apple-silicon Linux VMs):

| Image | Built from | Notes |
|---|---|---|
| `ghcr.io/<owner>/<repo>/api` | `api/Dockerfile` | Also run as the **worker** (same image, different command) |
| `ghcr.io/<owner>/<repo>/web` | `web/Dockerfile` | Hostnames are configured at runtime (`ALLOWED_HOSTS`), nothing is baked in |
| `ghcr.io/<owner>/<repo>/claude` | `claude/Dockerfile` | Contains the Claude Code CLI, installed unpinned |
| `ghcr.io/<owner>/<repo>/gateway` | `gateway/Dockerfile` | Caddy with the Coraza WAF module and the Caddyfile (sso front door) |

**Why a release takes the time it does.** The four images build in parallel and the slowest one sets the total. Each is built for `linux/amd64` only.
Building `arm64` as well ran under QEMU emulation on an x86 runner, which made compiling Caddy with the Coraza WAF module take ~20 minutes
(a release went from ~8 to ~22 minutes when the gateway was added), so it was dropped; `gateway/Dockerfile` still cross-compiles
(`--platform=$BUILDPLATFORM` plus `GOOS/GOARCH`), so adding `linux/arm64` back to `platforms:` in `release.yml` (and a `docker/setup-qemu-action` step for the other images)
costs little for the gateway. Every image uses a GitHub Actions layer cache between releases, and the `claude` image installs the unpinned Claude Code CLI as its last layer with a
`CLI_REV` build argument (the run id) so the CLI is refreshed on every release while the apt and pip layers stay cached.

Publishing happens **only when you push a tag that starts with `v`**. Nothing is published from branches or pull requests.

| Tag you push | Image tags |
|---|---|
| `v1.2.3` | `1.2.3`, `1.2`, `v1.2.3`, `latest` |
| any other `v*` (for example `v2-beta`) | the tag itself, and `latest` |

To release: `git tag v1.0.0 && git push origin v1.0.0`. CI runs first; if it fails nothing is published.

The same run also creates a **GitHub Release** with `papergrader-v1.0.0.tar.gz` attached. It holds `run.sh`,
`docker-compose.yml`, `.env.example`, `proxy/nginx.conf`, the certificate and Claude-login scripts, with the release tag baked
into `run.sh` (`VERSION=`; printed on every call). On `up`/`restart` `run.sh` compares the `org.opencontainers.image.version`
label of the local image (set by the release workflow) with the latest GitHub release (3 s timeout, silent if it fails)
and prints a hint to run `./run.sh pullup` when a newer one exists. The bundle's compose file is rewritten to name the published images with the tag
`latest` (`ghcr.io/<owner>/<repo>/api:latest` and so on), so `./run.sh pull` knows exactly what to download. The
Dockerfiles stay in the repository: only CI uses them. A server never builds anything.

### Running a release on a server

```sh
tar xzf papergrader-v1.0.0.tar.gz && cd papergrader-v1.0.0
./run.sh --domain lan.internal --admin-email me@example.edu   # first run: writes .env and the gateway secrets, makes the certificate, pulls, starts
./run.sh login                                               # sign Claude in once
# then: add <DOMAIN>, auth.<DOMAIN> and lldap.<DOMAIN> to /etc/hosts, open https://<DOMAIN>:5904, sign in as admin (LLDAP_ADMIN_PASSWORD in .env)
```

`run.sh` needs only Podman (it installs `podman-compose` into `./.venv` if missing). It generates `JWT_SECRET`, `CLAUDE_RUNNER_TOKEN`, `DB_PASSWORD`, the
LLDAP and Tinyauth secrets and the LDAP admin password (printed once), sets `AUTH_MODE`, `DOMAIN`, `HTTPS_PORT`, `EXTRA_DOMAINS` (when given; it also writes the gitignored `docker-compose.domains.yml` and `gateway/sites/domains.caddy` on every run), `COOKIE_SECURE`, `ALLOWED_HOSTS` and
`PUBLIC_BASE_URL`, makes the self-signed gateway certificate in `gateway/tls/`, and never overwrites an existing `.env`. The release bundle
carries `docker-compose.yml` pointing at the published `api`, `web`, `claude` and `gateway` images. Other commands: `up`, `down`, `restart`, `pull`, `pullup` (down, pull, up: the way to update to the newest release),
`status`, `logs [service]`, `login`, `migrate` (manual: copy old users into LDAP). In a git checkout (it has `./api`) it builds the images locally instead.

### Running the published images

The compose file builds locally by default. Point it at the registry instead:

```sh
export PAPERGRADER_REGISTRY=ghcr.io/<owner>/<repo>   # lower-case
export PAPERGRADER_TAG=1.0.0    # a release bundle already defaults to latest; set this only to pin
docker compose pull && docker compose up -d --no-build
```

GHCR packages are **private** by default: either make them public in the package settings or `docker login ghcr.io`
on the server. Because the Claude CLI is installed unpinned, two builds of the `claude` image can contain different
CLI versions; pin the tag you deploy. `.github/dependabot.yml` opens weekly update PRs, which go through the same CI.

### First push to GitHub

```sh
git remote add origin git@github.com:<owner>/<repo>.git
git add -A && git commit -m "Initial commit"      # the hook runs the tests
git push -u origin main
```

The workflows were checked with `actionlint` and every step's command was run locally, but they have not run on
GitHub yet. Watch the first run.
