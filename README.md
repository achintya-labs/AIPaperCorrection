# Paper Grader

TAs verify AI-graded, scanned answer sheets. Claude (via the Claude Code CLI) transcribes each page, works out
which questions are on it, grades every question against a rubric with feedback, and reports a confidence for
each page and each grade. TAs then approve or edit; their edits are stored separately (`final_*`) so the AI's
original output is always kept.

```
web/      Astro (React islands) UI. Also proxies /api/* to the API so the browser is same-origin.
api/      FastAPI + SQLAlchemy/Alembic: auth, rubrics, import, review, export. Also runs as the worker.
claude/   The `claude` container: Claude Code CLI + the Claude login + a small authenticated HTTP runner.
proxy/    nginx.conf for the HTTPS front door (the only service published on the network)
scripts/  claude-login.sh, make-cert.sh, make-sample-data.py, export-openapi.py, ...
samples/  A small sample exam (paper, rubric, answer sheets, CSVs) for trying the system
run.sh    One-command launcher (podman + podman-compose): sets itself up, writes .env, makes a certificate, starts
docs/     Full documentation (start at docs/README.md)
```

## Quick start

Needs only **Podman** (rootless, with subuid/subgid ranges). `run.sh` installs `podman-compose` into `./.venv` itself if it
isn't already on your machine. From a release bundle (`papergrader-vX.Y.Z.tar.gz` on the GitHub Releases page):

```sh
./run.sh --host grader.example.edu --admin-email me@example.edu   # writes .env with fresh secrets and a
                                                                  # self-signed certificate, pulls, starts
./run.sh login                                                    # once: sign Claude in
./run.sh pullup                                                   # later: update to the latest release
```

Commands: `up`, `down`, `restart`, `pull`, `pullup`, `status`, `logs [service]`, `login`, `env`.
Images are tagged `latest`, so `pullup` (down, pull, up) always moves to the newest release; The release version is baked into
`run.sh` and printed on every call; on `up` it also tells you when a newer release is out. From a git checkout (`run.sh up` builds the images locally the first time):

```sh
./scripts/setup-dev.sh   # once: repo-local .venv (podman-compose, pytest), web deps, commit hook
cp .env.example .env     # set CLAUDE_RUNNER_TOKEN (openssl rand -hex 32), JWT_SECRET,
                         # BOOTSTRAP_ADMIN_*, ALLOWED_HOSTS, COOKIE_SECURE=true
./scripts/make-cert.sh   # self-signed certificate for the HTTPS proxy (replace later with a real one)
./scripts/compose.sh up --build -d   # podman-compose from this repo; Docker: docker compose -p papergrader up --build -d
./scripts/claude-login.sh            # once: sign Claude in inside the `claude` container
```

Open **https://localhost:8443** (self-signed: the browser warns once) and sign in with the bootstrap admin. Then:
create an exam → write its rubric → import papers → start grading (per paper, a selection, or everything) → the worker grades them → TAs review from **My queue**.
Optional: sign-in with Google ([how-it-runs.md](docs/how-it-runs.md#sign-in-with-google)).

Want something to try? [`samples/`](samples/README.md) has a small exam: question paper, rubric, four handwritten-style
answer sheets and the CSVs for every import mode (`scripts/make-sample-data.py` regenerates it).

`./scripts/compose.sh` runs the **podman-compose installed in this repo's `.venv`**, so there's nothing to install system-wide. `make help` lists the shortcuts.

## Documentation

| | |
|---|---|
| [docs/how-it-runs.md](docs/how-it-runs.md) | Run, configure (every environment variable), Claude login, telemetry, operations, troubleshooting |
| [docs/architecture.md](docs/architecture.md) | Components, data model, design decisions, trust boundaries |
| [docs/workflows.md](docs/workflows.md) | Flowcharts: lifecycle, states, import, grading pipeline, review conflicts, auth path |
| [docs/api.md](docs/api.md) | API reference with examples; [docs/openapi.json](docs/openapi.json) is the generated spec |
| [docs/development.md](docs/development.md) | Repo-local tooling, tests, the commit gate, CI and publishing images |
| [docs/libraries.md](docs/libraries.md) | Suggested standard libraries |

Data lives in named volumes and survives restarts and crashes (see [how-it-runs.md](docs/how-it-runs.md#restarts-reboots-and-durability)).

Two things to know before you deploy:

- **`ALLOWED_HOSTS`** (runtime, in `.env`): set it to the hostname(s) users browse to, or uploads fail the cross-site
  check. Over HTTPS (the default setup) also set `COOKIE_SECURE=true`. `run.sh` does both for you.
- A Claude **subscription login** is meant for interactive personal use and has usage limits; check the terms
  before running a whole course on one. An API key (`ANTHROPIC_API_KEY`) is the supported route for an
  automated service.

## Tests, commits and CI

```sh
make test      # API (~8 s), web unit tests, type check, openapi.json check
make test-pg   # Postgres-only tests against a throwaway container
```

A git hook (`.githooks/pre-commit`, installed by `./scripts/setup-dev.sh`) runs the relevant tests before every commit
and blocks the commit if they fail. GitHub Actions runs the same checks on every pull request, builds the Docker images,
and publishes them to GHCR **only when you push a `v*` tag** (which also creates a GitHub Release with the bundle
above). See [docs/development.md](docs/development.md).
