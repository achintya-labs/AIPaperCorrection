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
| `make up` / `make down` / `make restart` | Start, stop and remove containers, restart them (volumes and data are kept) |
| `make rebuild` | Rebuild the `api`, `worker` and `web` images and recreate those containers |
| `make logs` / `make ps` | Follow logs, list containers |
| `make ci-lint` | Lint the GitHub workflows offline |

### Running the stack

`./scripts/compose.sh <args>` runs compose with the **podman-compose in this repo's `.venv`**, with the project name
fixed to `papergrader` so containers and volumes are the same whichever way you start the stack:

```sh
./scripts/compose.sh up --build -d
./scripts/compose.sh logs -f worker
COMPOSE_RUNTIME=docker ./scripts/compose.sh up -d     # the same, with `docker compose`
```

`./scripts/claude-login.sh` uses this wrapper automatically when `.venv` exists.

## Tests

| Suite | Where | Runs on | Time |
|---|---|---|---|
| API (auth, import, grading, review, deletion, runner, docs drift) | `api/tests` | SQLite, a stand-in `claude` executable | ~8 s |
| Web unit tests (table search, cross-site request check, import button state, login errors) | `web/src/lib/*.test.ts` | Node's built-in runner | <1 s |
| Type check and build | `web/` | `astro sync` (generates the `astro:*` types), `tsc`, `astro build` | ~10 s |
| Postgres-only (foreign keys, row locks, job claiming, migrations) | `api/tests_pg` | a real Postgres; skipped unless `PG_TEST_URL` is set | ~2 s |

The API suite is fast because the tests hash passwords at bcrypt's minimum cost (`BCRYPT_ROUNDS=4`, set in
`api/tests/conftest.py`); production keeps the default of 12. SQLite does not enforce foreign keys and ignores
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
| **Compose and workflows** | `docker compose config` parses with local and with published images; `run.sh` and `scripts/*.sh` parse; `actionlint` on the workflows |
| **Image builds** (pull requests only) | Builds the `api`, `web` and `claude` Dockerfiles without pushing, so a broken Dockerfile fails before merge |

## Publishing images

`.github/workflows/release.yml` runs the whole CI first (`uses: ./.github/workflows/ci.yml`) and **only then** builds
and pushes `linux/amd64` and `linux/arm64` images to GitHub Container Registry:

| Image | Built from | Notes |
|---|---|---|
| `ghcr.io/<owner>/<repo>/api` | `api/Dockerfile` | Also run as the **worker** (same image, different command) |
| `ghcr.io/<owner>/<repo>/web` | `web/Dockerfile` | Hostnames are configured at runtime (`ALLOWED_HOSTS`), nothing is baked in |
| `ghcr.io/<owner>/<repo>/claude` | `claude/Dockerfile` | Contains the Claude Code CLI, installed unpinned |

Publishing happens **only when you push a tag that starts with `v`**. Nothing is published from branches or pull requests.

| Tag you push | Image tags |
|---|---|
| `v1.2.3` | `1.2.3`, `1.2`, `v1.2.3`, `latest` |
| any other `v*` (for example `v2-beta`) | the tag itself, and `latest` |

To release: `git tag v1.0.0 && git push origin v1.0.0`. CI runs first; if it fails nothing is published.

The same run also creates a **GitHub Release** with `papergrader-v1.0.0.tar.gz` attached. It holds `run.sh`,
`docker-compose.yml`, `.env.example`, `proxy/nginx.conf`, the certificate and Claude-login scripts, and a `release.env`
that pins the registry and version. The Dockerfiles stay in the repository: only CI uses them. A server never builds
anything. `docker-compose.yml` names the images (`${PAPERGRADER_REGISTRY}/api:${PAPERGRADER_TAG}` and so on), so
`compose pull` knows exactly what to download.

### Running a release on a server

```sh
tar xzf papergrader-v1.0.0.tar.gz && cd papergrader-v1.0.0
./run.sh --host grader.example.edu --admin-email me@example.edu   # first run: writes .env, makes a certificate, pulls, starts
./run.sh login                                                    # sign Claude in once
```

`run.sh` needs only Docker or rootless Podman. It generates `JWT_SECRET`, `CLAUDE_RUNNER_TOKEN`, `DB_PASSWORD` and the
first admin password (printed once), sets `COOKIE_SECURE`, `ALLOWED_HOSTS` and `PUBLIC_BASE_URL`, and never overwrites an
existing `.env` or certificate. Other commands: `status`, `logs [service]`, `stop`, `down`, `pull`. In a git checkout
(no `release.env`) it builds the images locally instead.

### Running the published images

The compose file builds locally by default. Point it at the registry instead:

```sh
export PAPERGRADER_REGISTRY=ghcr.io/<owner>/<repo>   # lower-case
export PAPERGRADER_TAG=1.0.0    # (run.sh does this for you from release.env)
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
