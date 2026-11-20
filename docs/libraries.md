# Suggested standard libraries

Nothing here is required: the system works as built. These are places where a well-known library would replace
hand-written code, close a risk, or make the project easier to maintain. Each row says what the code does today
and where, so you can judge the trade-off.

**Priority:** **High** = a risk or a clear cost saving, worth doing soon · **Medium** = good value when you next
touch that area · **Low** = only if the need appears. **Effort:** S under an hour or two, M about a day, L multi-day.

## Recommended first five

1. **Check the PyMuPDF licence** (AGPL) before deploying to others. [Details](#pdf-and-images).
2. **Move off `python-jose` 3.3** (to PyJWT, or at least ≥ 3.4.0, which fixes two published CVEs) and consider Argon2 (`pwdlib`) for passwords. Small, security-relevant.
3. **Generate the TypeScript API client** from `docs/openapi.json`; the hand-written types can drift.
4. **Add `slowapi` login rate limiting**, because the login endpoint is unauthenticated.
5. **Use `testcontainers` for Postgres in the test suite**, because the current SQLite tests cannot exercise
   `FOR UPDATE SKIP LOCKED`.

## Backend

### Auth and security

| Today | Suggestion | Why | Effort | Priority |
|---|---|---|---|---|
| `python-jose` for JWTs (`api/app/auth/__init__.py`), pinned `==3.3.*` in `api/requirements.txt` | **PyJWT** (or, at minimum, bump `python-jose` to **3.4.0 or later**) | The pinned 3.3 range predates the fixes for **CVE-2024-33663** (algorithm confusion with some asymmetric key formats) and **CVE-2024-33664** (JWE decompression DoS), both fixed in 3.4.0. Exposure here is probably low (HS256 with a shared secret, only JWS tokens decoded), but it is trivially avoided: the code only needs `encode`/`decode`, which PyJWT provides, and PyJWT is the more widely used library | S | High |
| Raw `bcrypt` (`hash_password`) | **pwdlib** with Argon2id (or `argon2-cffi`) | Argon2id is the current recommended password hash and has no 72-byte limit (the server currently has to reject longer passwords); `pwdlib` also supports verifying old bcrypt hashes while rehashing on login | S | Medium |
| No login throttling | **slowapi** (or a reverse-proxy rate limit) | `/auth/login` is public, and `PUT /auth/me/password` accepts guesses of the current password from any signed-in session; both are unlimited | S | High |
| Password rules (8+ characters, at most 72 bytes) enforced server-side by a small validator | **zxcvbn** (strength estimate) and/or the **Pwned Passwords** k-anonymity API | Length alone accepts `password123`; a strength or breach check catches the common bad choices. Optional, and the breach check sends a hash prefix to a third party | S | Low |
| Local users only | **Authlib** (OIDC/SAML-style SSO) or **fastapi-users** | Schools usually have SSO; fastapi-users also gives user management, password reset and e-mail verification out of the box | L | Low, until you have more than a few TAs |
| Secrets in `.env` | Podman/Docker **secrets**, or **sops** for committed encrypted config | Keeps tokens out of process listings and shell history | M | Low |

### PDF and images

| Today | Suggestion | Why | Effort | Priority |
|---|---|---|---|---|
| **PyMuPDF** (`api/app/pdf/__init__.py`) | **pypdfium2** to render pages and **pypdf** to split/count | PyMuPDF is licensed **AGPL-3.0** (a commercial licence is sold separately). Artifex sells the commercial licence and says to contact them if you cannot meet the AGPL's terms. Running it inside a network service you offer to others may trigger the AGPL's source-offer obligations; this is not legal advice, so check with Artifex or your institution's counsel. pypdfium2 (Apache-2.0/BSD) and pypdf (BSD) are permissive. The module is small and isolated, and the global lock design carries over (pdfium is also not thread-safe) | M | **High if this is deployed beyond your own use** |
| Pages sent at a fixed `RENDER_DPI` | **Pillow** (resize/compress) and optionally **opencv-python-headless** (deskew, denoise, contrast) | Cleaner scans improve handwriting recognition and smaller images cut model cost. Do it in the worker between render and send | M | Medium |

### Reliability and jobs

| Today | Suggestion | Why | Effort | Priority |
|---|---|---|---|---|
| Hand-written retry loop (`GraderProvider.grade`) and a single attempt for HTTP to the runner (`RemoteExecutor`) | **tenacity** | Declarative retry/backoff/jitter for transient runner or network errors; keep the existing "retry with the rejection reason" logic for invalid model output | S | Medium |
| Postgres as the queue (`jobs/queue.py`: `status='queued'`, `SKIP LOCKED`, `claimed_at`) | **Procrastinate** (Postgres-native task queue) if you outgrow it; **arq** or **Celery** with Redis beyond that | The current design is simple and correct for classroom volume. A library adds retries with delay, scheduling, per-job visibility and a worker CLI, at the price of a dependency. Don't switch until you need those | L | Low |
| `LocalStorage` behind a `Storage` protocol | **fsspec** (+ **s3fs**) or **boto3** | Same protocol, S3/MinIO backend so several hosts can share scans | M | Low |

### Observability

| Today | Suggestion | Why | Effort | Priority |
|---|---|---|---|---|
| `logging` in the worker only | **structlog** | Structured, JSON logs with submission/user ids on every line | S | Medium |
| No metrics | **prometheus-fastapi-instrumentator** | Request rates, latencies and error rates for free; add counters for jobs graded/failed | S | Medium |
| Claude Code already exports OTel (see [how-it-runs.md](how-it-runs.md#telemetry)) | **OpenTelemetry Python SDK** + `opentelemetry-instrumentation-fastapi` / `-sqlalchemy` | One trace from browser request to grading job, in the same collector as the Claude metrics | M | Low |
| `/health` is liveness only | Add a readiness check (DB reachable, runner reachable) | Lets the orchestrator and load balancer distinguish "up" from "usable" | S | Medium |

## Frontend

| Today | Suggestion | Why | Effort | Priority |
|---|---|---|---|---|
| Hand-written `web/src/lib/api.ts` and `types.ts` | **openapi-typescript** + **openapi-fetch**, fed by `docs/openapi.json` (`npx openapi-typescript docs/openapi.json -o web/src/lib/schema.d.ts`) | Types come from the API itself so they cannot drift; add the generation to CI next to `scripts/export-openapi.py --check` | M | High |
| Manual `useEffect` + `setInterval` fetching in `Queue.tsx`, `ExamSubmissions.tsx`, `Review.tsx` | **TanStack Query** | Caching, request de-duplication, background refetch/polling, loading and error states, optimistic updates for the grade editor | M | Medium |
| Hand-rolled form state and validation (`RubricEditor.tsx`, `Users.tsx`, `ImportWizard.tsx`) | **react-hook-form** + **zod** | Less boilerplate; the zod schema can mirror the API rules (unique question numbers, criteria ≤ max) | M | Medium |
| Plain `<table>` | **TanStack Table** | Sorting, filtering and virtualisation once a course has hundreds of papers | M | Low |
| No linting/format for the web app | **ESLint** + **Prettier**, `@astrojs/check` | Catches `.astro` template errors that `tsc --noEmit` does not cover | S | Medium |

## Testing and quality

| Today | Suggestion | Why | Effort | Priority |
|---|---|---|---|---|
| API tests run on SQLite (`api/tests/*`); a small Postgres-only suite (`api/tests_pg`, run in CI and by `make test-pg`) covers foreign keys, row locks and job claiming | **testcontainers-python** to run the Postgres suite without a script or CI service | SQLite ignores `FOR UPDATE SKIP LOCKED`, and enum/JSON behaviour differs; test the claim logic and migrations against the real database | M | High |
| Hand-written API tests | **schemathesis** against `docs/openapi.json` | Property-based fuzzing finds 500s and schema mismatches no one thought to test | S | Medium |
| End-to-end checks (full stack through the proxy, and a real browser) were run during development but **are not in the repo** | **@playwright/test** with a compose-based fixture | Turns the manual checks into a regression suite: login, import, review, conflict, logout | M | High |
| `FakeProvider` and a stand-in `claude` script | Keep, add **respx** / **pytest-httpx** for the runner client | Fine-grained HTTP failure tests (timeouts, 5xx) without sockets | S | Low |
| No linter/type checker for Python | **ruff** (lint+format) and **mypy** or **pyright**, wired into the existing commit hook (`.githooks/pre-commit`) or the **pre-commit** framework | Cheap consistency and early bug detection | S | Medium |

## Build and operations

| Today | Suggestion | Why | Effort | Priority |
|---|---|---|---|---|
| `requirements.txt` with `fastapi==0.115.*` style ranges and `pip install` | **uv** (`uv lock`, `uv sync`) or **pip-tools** | Reproducible, hash-pinned builds and much faster image builds. (`web/` already has `package-lock.json`) | S | Medium |
| `claude` image is ~1.5 GB (Debian `nodejs`/`npm` packages) | Base it on the official **`node`** image and add Python, or install Node via a slimmer route | Smaller, faster to pull and rebuild | S | Low |
| TLS is left to "a reverse proxy" | **Caddy** (automatic HTTPS) or **Traefik** in the compose file | Forwards `Host`/`X-Forwarded-Proto` correctly, which the web service's CSRF check depends on | M | Medium |
| One shared env block for `api` and `worker` | Split `x-app-env` so the worker doesn't receive `JWT_SECRET` and the bootstrap password | Least privilege (see [architecture.md](architecture.md#components)) | S | Low |
| Settings like `MAX_UPLOAD_MB`, `RENDER_DPI`, `GRADER_MAX_ATTEMPTS` not wired through compose | Add them to `x-app-env` | Make documented knobs actually settable (see [how-it-runs.md](how-it-runs.md#configuration-reference)) | S | Medium |

## Things deliberately kept

- **FastAPI, SQLAlchemy 2, Alembic, pydantic-settings, Astro, React**: already the standard choices.
- **The `GraderProvider` / `ClaudeExecutor` abstractions**: they exist so a library or a different model
  provider (including the Anthropic Messages API) can be swapped in without touching the rest. Where the
  Anthropic SDK is the better route (direct API instead of the CLI), implement `anthropic_api.py`'s `_attempt`
  rather than changing callers.
- **Postgres-as-queue** for now; see the Procrastinate note above.
