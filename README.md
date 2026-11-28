# Paper Grader

TAs verify AI-graded, scanned answer sheets. Claude (via the Claude Code CLI) transcribes each page, works out
which questions are on it, grades every question against a rubric with feedback, and reports a confidence for
each page and each grade. TAs then approve or edit; their edits are stored separately (`final_*`) so the AI's
original output is always kept.

## How it fits together

```
 browser ──HTTPS :5904──► caddy (Coraza WAF + OWASP CRS) ──► tinyauth (login) ──► web ──► api ──► db (Postgres)
                                                                 │                          │        ▲
                                                       lldap (users + groups,               └─ worker ─┘──► claude ──► Anthropic
                                                       stored in the same Postgres)
```

- **One door.** The Caddy gateway is the only thing published on the network. `web`, `api`, `db`, `lldap` and `claude` publish no port.
- **Login is not in the app.** Tinyauth signs people in (LDAP users, optionally Google). The app trusts the `Remote-Email` the gateway
  adds, looks the person up in LDAP, and takes their **role from LDAP groups**: `papergrader_admins` are admins, `papergrader_tas` are TAs,
  anyone else is "not enrolled". Users are created, renamed and given passwords in **LLDAP**, not in the app.
- `AUTH_MODE=local` keeps the app's own password login behind nginx, for development ([details](docs/how-it-runs.md#single-sign-on-the-default-auth_modesso)).

```
web/       Astro (React islands) UI. Also proxies /api/* to the API so the browser is same-origin.
api/       FastAPI + SQLAlchemy/Alembic: rubrics, import, review, export, LDAP sign-in. Also runs as the worker.
claude/    The `claude` container: Claude Code CLI + the Claude login + a small authenticated HTTP runner.
gateway/   Caddy + Coraza WAF image and Caddyfile (the sso front door); gateway/tls/ holds its certificate.
proxy/     nginx.conf for AUTH_MODE=local (the old HTTPS front door)
scripts/   claude-login.sh, make-cert.sh, make-sample-data.py, export-openapi.py, ...
samples/   A small sample exam (paper, rubric, answer sheets, CSVs) for trying the system
run.sh     One-command launcher (podman + podman-compose): sets itself up, writes .env, makes the certificate, starts
docs/      Full documentation (start at docs/README.md)
```

## Quick start

You need **Podman** (rootless, with subuid/subgid ranges; `run.sh` prints the fix if they are missing). `run.sh` installs
`podman-compose` into `./.venv` itself if you don't have it. From a release bundle (`papergrader-vX.Y.Z.tar.gz` on the GitHub
Releases page) or a git checkout (the first start builds the images, which takes a few minutes):

```sh
./run.sh --domain lan.internal --admin-email me@example.edu   # first run: writes .env with fresh secrets, then starts everything
./run.sh login                                                # once: sign Claude in (inside the claude container)
```

1. **Hosts file.** Every machine that browses to it needs a line for the three names, with this machine's address
   (`127.0.0.1` if you browse from the same machine):
   `127.0.0.1  lan.internal  auth.lan.internal  lldap.lan.internal`
2. **Open** `https://lan.internal:5904`. The certificate is self-signed: accept the warning for `lan.internal` and again for `auth.lan.internal` (signing in
   redirects there). You land on the login portal.
3. **Sign in as `admin`.** The password is `LLDAP_ADMIN_PASSWORD` in `.env` (also printed on the first run:
   `grep ^LLDAP_ADMIN_PASSWORD .env`). That account is an app admin.
4. **Add people** at `https://lldap.lan.internal:5904` (log in as `admin`): Users, create a user, set a password, then add them to
   `papergrader_tas` or `papergrader_admins` ([steps](#people-and-roles)).
5. **Grade.** Create an exam, write its rubric, import papers, start grading, review ([workflows](#day-to-day-workflows)).

Want something to try? [`samples/`](samples/README.md) has a small exam: question paper, rubric, four handwritten-style answer sheets
and the CSVs for every import mode (`scripts/make-sample-data.py` regenerates it).

### Commands

| Command | Does |
|---|---|
| `./run.sh up` | start or recreate everything (rebuilds images in a checkout; `podman-compose up -d --force-recreate`) |
| `./run.sh down` / `restart` | stop and remove the containers (data stays in volumes) / down then up |
| `./run.sh pull` / `pullup` | download the latest release images / down, pull, up (the way to update a release bundle) |
| `./run.sh status`, `logs [service]` | `podman-compose ps`, follow logs (e.g. `logs caddy`, `logs lldap`) |
| `./run.sh login` | sign Claude in once (`claude-login.sh`) |
| `./run.sh migrate` | **manual, never part of `up`**: copy the users of an older install into LLDAP ([below](#moving-users-from-an-older-install)) |
| `./run.sh caddy FILE` | put a new Caddyfile into the running gateway and reload it, no rebuild (`--show`, `--revert`; [details](docs/how-it-runs.md#changing-the-gateways-caddyfile-on-a-live-server)) |
| `./run.sh compose ARGS` | any `podman-compose` command for this project with the right profile, e.g. `./run.sh compose exec -T db psql -U grader grader` |
| `./run.sh env` | where `.env` is, the sign-in mode, the admin email |

Options (first run only; they are stored in `.env`): `--domain D` (sso base domain), `--admin-email E`, `--https-port P` (default 5904),
`--auth-mode sso|local`, `--host H` (local mode). The release version is baked into `run.sh` and printed on every call; on `up` it
also says when a newer release is out.

## Day-to-day workflows

### Admin: run an exam
1. **Exams → create** the exam, then write its **Rubric** (questions, max points, expected answers, criteria). *JSON template* downloads a
   starting file; *Download PDF* prints the rubric.
2. **Import** the scans: one PDF per student named `<roll>.pdf`, or one combined PDF with a CSV of page ranges, or *auto-split* (Claude finds
   the boundaries and roll numbers). Preview first, assign TAs while importing or later.
3. **Papers tab:** nothing is graded automatically. *Grade* one paper, tick several and *Grade selected*, or *Grade all ungraded*.
   Tick papers to assign TAs in bulk (add, set, remove, or deal them out round robin across chosen TAs), export PDFs (graded papers annotated, ungraded as the original scan), or delete.
4. Papers go `uploaded` → `queued` → `grading` → `ready for review`. Check Claude's remaining plan quota with **Admin → Models → Check quota**.
5. Export grades (CSV) and annotated PDFs (ZIP) from the Papers tab when the TAs are done.

### TA: review
Sign in and open **My queue**: only papers assigned to you. Review each question (Claude's score, feedback, confidence and a private note for
you), edit or approve, and complete the paper. Your edits are kept next to Claude's untouched output.

### People and roles
Everything about accounts happens in **LLDAP** (`https://lldap.lan.internal:5904`, log in as `admin`). The app's **Admin** page only lists who is
enrolled and links there.

- **Add someone:** Users → *Create a user* (user id, email, name, password) → open them → *Group memberships* → add `papergrader_tas`
  (or `papergrader_admins`). They can sign in right away; they show up in the app's Admin list and TA pickers within about 30 seconds, even
  before their first sign-in, so you can assign them papers first.
- **Change role:** move them between the two groups. **Remove access:** remove them from both (or delete the user); they get "not enrolled",
  and their papers, grades and review history stay.
- **Change name, email or password:** edit the user in LLDAP as `admin`. The user id (what they type at login) can't change. Only administrators can
  open LLDAP at all: the gateway shows everyone else a 403 page (the email is people's identity in the app, so they must not edit it). The ids allowed
  in are `LLDAP_ADMIN_USERS` in `.env` (default `admin`; list every LLDAP admin, e.g. `admin|alice`). People ask an admin to set or reset their password.
- Anyone in LDAP who is in neither group can sign in at the gateway but sees *"Your account is not enrolled in Paper Grader"*.

### Updating
- **Release bundle:** `./run.sh pullup` (down, pull `latest`, up). Your data and `.env` are untouched.
- **Git checkout:** `git pull && ./run.sh up` (it rebuilds the changed images first).

### Moving users from an older install
Installs made before single sign-on had their own user table. `./run.sh up` switches such an install to sso and **does not touch your users**.
When you are ready, run **`./run.sh migrate`**: it exports the active users and imports them into LLDAP (each in the group matching their old
role). It is manual on purpose (a reset never repeats it) and safe to run again. Everyone keeps their papers and grades: on first sign-in
they are matched by email. **Passwords can't be carried over**: set one per person in LLDAP, or use Google sign-in
(`OAUTH_WHITELIST` in `.env`).

### Resetting and starting over
`./run.sh down` keeps all data. To wipe a stack, remove its volumes ([Reset](docs/how-it-runs.md#reset)); delete `.env` too for new secrets.
Neither re-runs a migration.

### Development without the gateway
`./run.sh --auth-mode local` runs the old password login behind nginx at `https://localhost:8443`; the API tests run in that mode. See
[docs/development.md](docs/development.md).

## Troubleshooting (the usual first-run problems)

| Symptom | Fix |
|---|---|
| Browser can't connect to `lan.internal:5904` | The hosts line is missing, or the gateway isn't up: `./run.sh status` should show `caddy`, `tinyauth`, `lldap` |
| Certificate warning / "invalid signature" | Expected once per name (self-signed). If it is a *signature* error, an old "Caddy Local Authority" certificate is cached: remove it in the browser's certificate manager |
| LLDAP shows "Invalid JWT" | Another LLDAP on the same domain (an old gateway) left a login cookie. Clear cookies for `lan.internal`, stop the old stack, or use another `--domain` |
| 403 with an empty page on something legitimate | The WAF blocked it. Find the rule: `./run.sh logs caddy \| grep "rule violation"`, then add a narrow exclusion in `gateway/Caddyfile` ([how](docs/how-it-runs.md#troubleshooting)) |
| "Your account is not enrolled in Paper Grader" | The person is in neither LDAP group; add them in LLDAP |
| Stack stuck at "Created" | A service waits on a health check: `./run.sh logs lldap`; fixed by `./run.sh up` again after pulling the latest compose file |

More in [docs/how-it-runs.md](docs/how-it-runs.md#troubleshooting).

## Documentation

| | |
|---|---|
| [docs/how-it-runs.md](docs/how-it-runs.md) | Run, configure (every environment variable), single sign-on, Claude login, telemetry, operations, troubleshooting |
| [docs/architecture.md](docs/architecture.md) | Components, data model, design decisions, trust boundaries |
| [docs/workflows.md](docs/workflows.md) | Flowcharts: lifecycle, states, import, grading pipeline, review conflicts, sign-in, people and roles, migration |
| [docs/api.md](docs/api.md) | API reference with examples; [docs/openapi.json](docs/openapi.json) is the generated spec |
| [docs/development.md](docs/development.md) | Repo-local tooling, tests, the commit gate, CI and publishing images |
| [docs/libraries.md](docs/libraries.md) | Suggested standard libraries |

Data lives in named volumes and survives restarts and crashes (see [how-it-runs.md](docs/how-it-runs.md#restarts-reboots-and-durability)).

Two things to know before you deploy:

- **`ALLOWED_HOSTS`** (runtime, in `.env`): the hostname(s) users browse to, or uploads fail the cross-site check. `run.sh` adds `<DOMAIN>` and every `EXTRA_DOMAINS` entry (more, unrelated domains serving the same app; see [how-it-runs.md](docs/how-it-runs.md)).
- A Claude **subscription login** is meant for interactive personal use and has usage limits; check the terms before running a whole course
  on one. An API key (`ANTHROPIC_API_KEY`) is the supported route for an automated service.

## Tests, commits and CI

```sh
make test      # API (~8 s), web unit tests, type check, openapi.json check
make test-pg   # Postgres-only tests against a throwaway container
```

A git hook (`.githooks/pre-commit`, installed by `./scripts/setup-dev.sh`) runs the relevant tests before every commit
and blocks the commit if they fail. GitHub Actions runs the same checks on every pull request, builds the Docker images,
and publishes them to GHCR **only when you push a `v*` tag** (which also creates a GitHub Release with the bundle
above). See [docs/development.md](docs/development.md).
