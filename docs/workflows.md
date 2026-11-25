# Workflows and flowcharts

Diagrams are [Mermaid](https://mermaid.js.org/); they render on GitHub, GitLab and in VS Code.
Source files are named next to each diagram so you can check them against the code.

1. [End-to-end lifecycle](#1-end-to-end-lifecycle)
2. [Submission states](#2-submission-states)
3. [Import decision flow](#3-import-decision-flow)
4. [Grading pipeline](#4-grading-pipeline)
5. [Review and edit conflicts](#5-review-and-edit-conflicts)
6. [Request path and authentication](#6-request-path-and-authentication)
7. [Claude login and readiness](#7-claude-login-and-readiness)
8. [Restarts and recovery](#8-restarts-and-recovery)

---

## 1. End-to-end lifecycle

Who does what, from an empty system to an exported gradebook.

```mermaid
flowchart TD
    subgraph setup["Admin: one-time setup"]
        A1["Start the stack"] --> A2["Sign Claude in once<br/>claude-login.sh"]
        A2 --> A3["Create TA accounts"]
        A3 --> A4["Create exam"]
        A4 --> A5["Write rubric<br/>questions, max points, criteria"]
    end

    subgraph imp["Admin: import papers"]
        B1["Upload PDFs + optional CSV"] --> B2["Preview<br/>dry_run=true"]
        B2 --> B3{"Preview OK?"}
        B3 -- "no: fix files or CSV" --> B1
        B3 -- yes --> B4["Import<br/>dry_run=false"]
    end

    subgraph auto["Automatic grading"]
        C1["Paper is queued"] --> C2["Worker renders pages"]
        C2 --> C3["Claude transcribes, finds questions per page,<br/>grades each question, reports confidence"]
        C3 --> C4{"Valid result?"}
        C4 -- yes --> C5["ready_for_review"]
        C4 -- no --> C6["failed + error message"]
    end

    subgraph rev["TA: verification"]
        D1["My queue<br/>least confident first"] --> D2["Open paper"]
        D2 --> D3["Check each question<br/>against the page image"]
        D3 --> D4{"Grade correct?"}
        D4 -- yes --> D5["Approve AI grade"]
        D4 -- no --> D6["Edit score and feedback"]
        D5 --> D7{"All questions checked?"}
        D6 --> D7
        D7 -- no --> D3
        D7 -- yes --> D8["Complete review"]
    end

    A5 --> B1
    B4 --> C1
    C5 --> D1
    C6 --> E1["Admin: Queue waiting and failed,<br/>or fix the cause first"]
    E1 --> C1
    D8 --> F1["Admin: export CSV"]
```

Notes:
- Papers imported **before** a rubric exists stay `uploaded`; use *Queue waiting & failed* on the exam's
  Papers tab (`POST /exams/{id}/queue`) after saving the rubric.
- An admin can regrade any finished paper later (see [state diagram](#2-submission-states)).

---

## 2. Submission states

`Submission.status` (`api/app/models/__init__.py`). A submission in `queued` *is* a pending job; there is
no separate queue table.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> queued: import with rubric
    [*] --> uploaded: import, no rubric

    uploaded --> queued: queue
    queued --> grading: worker claims
    grading --> ready_for_review: saved
    grading --> failed: error
    grading --> queued: worker restart or stale
    failed --> queued: queue or regrade

    ready_for_review --> reviewed: complete
    reviewed --> reviewed: complete again
    ready_for_review --> queued: regrade
    reviewed --> queued: regrade
```

Rules behind the arrows:

| Transition | Where | Condition |
|---|---|---|
| import → `queued` / `uploaded` | `routes/imports.py` | `queued` only if the exam already has a rubric |
| `queued` → `grading` | `jobs/queue.py: claim_next` | `SELECT … FOR UPDATE SKIP LOCKED`, oldest id first; sets `claimed_at` |
| `grading` → `queued` (worker restarted) | `jobs/queue.py: recover_own` | At worker start-up: jobs this worker (`claimed_by` = its container hostname or `WORKER_ID`) had claimed. Immediate |
| `grading` → `queued` (stale) | `jobs/queue.py: recover_stale` | Anyone's job with `claimed_at` older than `GRADER_TIMEOUT_SECONDS × GRADER_MAX_ATTEMPTS + 120 s`; checked every minute |
| `grading` → `failed` | `jobs/queue.py: _fail` | any exception; message stored in `submission.error` and a `GradingRun` row |
| → `reviewed` | `routes/review.py: complete_review` | at least one grade and none `pending`, otherwise 409 |
| regrade | `routes/grading.py: regrade` | 409 if `queued`/`grading`; 409 if any grade is `reviewed`/`overridden` unless `force=true` |

A **grade** has its own small lifecycle: `pending` (Claude's suggestion, unchecked) → `reviewed` (TA saved
values identical to Claude's) or `overridden` (TA changed score or feedback). TAs can edit grades only while
the submission is `ready_for_review` or `reviewed`.

---

## 3. Import decision flow

`POST /exams/{exam_id}/imports` (`api/app/routes/imports.py`). Always preview with `dry_run=true` first;
the same request with `dry_run=false` commits.

```mermaid
flowchart TD
    S["Request: files, optional mapping_csv,<br/>assign, reviewers_per_paper, dry_run"] --> X{"Exam exists?"}
    X -- no --> E404["404"]
    X -- yes --> P["Parse CSV if given<br/>student_id required"]
    P --> PV{"CSV valid?<br/>no duplicate ids,<br/>first_page and last_page together,<br/>integers"}
    PV -- no --> E422["422"]
    PV -- yes --> M{"Any CSV row has<br/>first_page / last_page?"}

    M -- "yes: combined PDF" --> M1{"Exactly one PDF?"}
    M1 -- no --> E422
    M1 -- yes --> M2["Cut the PDF into one part per row<br/>pages are 1-based, inclusive"]
    M2 --> M3{"Ranges in bounds,<br/>no overlaps?"}
    M3 -- no --> E422
    M3 -- yes --> I

    M -- "no: one PDF per student" --> F1["Student id = file name without .pdf"]
    F1 --> F2{"Id empty or<br/>repeated in this upload?"}
    F2 -- yes --> ERR["Item status: error"]
    F2 -- no --> I

    I["Per item checks"] --> I1{"Student already has a<br/>submission for this exam?"}
    I1 -- yes --> CON["Item status: conflict, skipped"]
    I1 -- no --> I2{"PDF readable,<br/>not encrypted?"}
    I2 -- no --> ERR
    I2 -- yes --> A{"Assignment mode"}

    A -- "csv (default if CSV has ta_emails)" --> A1{"Every ta_email is an<br/>active TA?"}
    A1 -- no --> ERR
    A1 -- yes --> OK
    A -- "round_robin" --> A2["Pick reviewers_per_paper TAs<br/>cycling through active TAs by id<br/>422 for the whole request if no active TAs"] --> OK
    A -- "none (default without ta_emails)" --> OK["Item status: ok"]

    OK --> D{"dry_run?"}
    CON --> D
    ERR --> D
    D -- yes --> R1["Return per-item preview<br/>nothing saved"]
    D -- no --> W["Save each ok PDF under a generated key<br/>create Student if new<br/>create Submission"]
    W --> W1{"Rubric exists?"}
    W1 -- yes --> Q["status = queued"]
    W1 -- no --> U["status = uploaded"]
    Q --> R2["Return result, created = number of ok items"]
    U --> R2
```

Details worth knowing:
- Item-level problems never fail the whole batch; only request-level problems (bad CSV, bad ranges) return 422.
- Stored file names are server-generated (`submissions/{exam}/{uuid}.pdf`); uploaded names are never used as paths.
- If saving fails part-way, the database transaction is rolled back and already-written files are deleted.
- Each file is limited by `MAX_UPLOAD_MB` (413 when exceeded).

---

## 4. Grading pipeline

What happens to one `queued` submission (`api/app/jobs/queue.py`, `api/app/grader/*`, `api/app/claude_runner.py`).

```mermaid
sequenceDiagram
    autonumber
    participant W as Worker thread
    participant DB as Postgres
    participant ST as Storage volume
    participant P as ClaudeCliProvider
    participant R as Runner in claude container
    participant C as claude CLI

    W->>DB: claim_next: oldest queued, SKIP LOCKED
    DB-->>W: submission now grading
    W->>DB: load rubric, exam model override
    W->>ST: read PDF
    W->>W: render pages to PNG, PyMuPDF under a lock
    W->>DB: release transaction before the long call

    loop up to GRADER_MAX_ATTEMPTS
        W->>P: grade pages, rubric, model
        P->>R: POST /run model, prompt, page PNGs, rubric.json, bearer token
        R->>R: validate file names, model, sizes
        R->>C: claude -p with fixed read-only flags in a temp dir
        C-->>R: JSON envelope on stdout
        R-->>P: returncode, stdout, stderr
        P->>P: parse structured_output, check schema
        P->>P: check_result: every page once, known questions, score within -max and max
        alt result accepted
            P-->>W: GradingOutcome
        else invalid output, retryable
            P->>P: add the rejection reason to the prompt and try again
        else timeout, CLI error or not logged in
            P-->>W: GraderError, no retry
        end
    end

    alt success
        W->>ST: save page PNGs
        W->>DB: replace Pages and Grades, add GradingRun with raw output
        W->>DB: status = ready_for_review, overall_confidence
    else any failure
        W->>DB: status = failed, error text, GradingRun with error
    end
```

Design points:
- **Only the `claude` container has the CLI and the credentials.** The runner builds the CLI flags itself
  (`build_command` in `grader/claude_cli.py`); a caller can choose the model and send files but cannot add
  flags such as shell access.
- **Retries are for bad output, not for infrastructure.** Schema or consistency problems are retryable and
  the reason is fed back to the model; timeouts, non-zero exits and `is_error` results fail immediately.
- **No open transaction during the model call**, which can take minutes.
- **Negative marking is allowed.** A question's score may be between minus `max_points` and `max_points`; the check
  in `check_result` rejects anything outside that, and a retryable rejection is fed back to the model.
- **Feedback and reviewer note are separate.** `feedback` is the layout the student sees; the optional `note` (stored as
  `ai_note`) is for the TA only and is never exported.
- **AI output is never overwritten by TAs**: it lands in `ai_*` columns; TA edits go to `final_*`.
- A regrade deletes the old `Page`/`Grade` rows and writes new ones; `GradingRun` rows accumulate as history.

---

### Auto-split of a combined PDF

`POST /exams/{id}/imports/auto-split` stages the PDF; a worker thread then runs `queue.process_split`
(`api/app/jobs/queue.py`, `grader/claude_cli.py: propose_split`, `pdf/__init__.py: contact_sheets`).

```mermaid
flowchart TD
    A["Worker claims a queued split job"] --> B["Render every page to a PNG<br/>100 dpi"]
    B --> C["Contact sheets: the top SPLIT_TOP_FRACTION of each page,<br/>about 6 per image, each tile labelled PAGE n"]
    C --> D{"More sheets than<br/>one request holds? (60)"}
    D -- no --> E["One request: all sheets inline in one message,<br/>no tools, 2 model turns"]
    D -- yes --> F["Overlapping chunks of 60 sheets,<br/>one request each, 1 sheet shared"]
    E --> G["check_split_result: in range, sorted, no overlap,<br/>roll numbers in the allowed list"]
    F --> H["Per chunk: same check, and splits must start inside the chunk"]
    H --> I["merge_chunk_splits: the overlap is cut in the middle,<br/>pieces touching at a cut join when the roll number matches or is blank"]
    I --> G
    G -- valid --> J["Job ready: the admin reviews and edits the proposal"]
    G -- invalid and attempts left --> K["Retry with the reason in the prompt"]
    K --> E
    G -- gives up --> L["Job failed with the message; the admin can retry"]
```

Why contact sheets in one message: the old approach told the CLI to open each page with its Read tool, one model round-trip
per page (160 for a 158-page scan, over two minutes even in a clean test and far longer under load, and past the context
window). Measured on a 158-page test PDF with Haiku: per-page reads 131 s and $0.25; all pages inline 32 s and $0.22;
contact sheets 26 s and a few cents.

## 5. Review and edit conflicts

Several TAs may be assigned to one paper. Each grade carries a `version`; an edit must quote the version it
read (compare-and-set in `routes/review.py: update_grade`).

```mermaid
sequenceDiagram
    autonumber
    participant A as TA A browser
    participant B as TA B browser
    participant API as API
    participant DB as Postgres

    A->>API: GET /submissions/7/detail
    API-->>A: grade 42, version 3
    B->>API: GET /submissions/7/detail
    API-->>B: grade 42, version 3

    A->>API: PATCH /grades/42 version 3, score 4, feedback A
    API->>DB: UPDATE grade 42 where version = 3, set version = 4
    DB-->>API: 1 row updated
    API-->>A: 200 grade 42, version 4, status overridden

    B->>API: PATCH /grades/42 version 3, score 2, feedback B
    API->>DB: UPDATE grade 42 where version = 3
    DB-->>API: 0 rows updated
    API-->>B: 409 message and current grade, version 4
    Note over B: UI keeps B's draft, shows A's edit,<br/>and uses version 4 for the next save
    B->>API: PATCH /grades/42 version 4, B's values
    API-->>B: 200 grade 42, version 5
```

The edit rules:

```mermaid
flowchart TD
    E["PATCH /grades/id<br/>version, final_score, final_feedback"] --> V{"Grade exists and<br/>submission visible to this user?"}
    V -- no --> N["404, same answer for missing and not yours"]
    V -- yes --> S{"Submission is<br/>ready_for_review or reviewed?"}
    S -- no --> C1["409 cannot be reviewed yet"]
    S -- yes --> M{"score within -max_points and max_points?"}
    M -- no --> U["422"]
    M -- yes --> Q{"version matches?"}
    Q -- no --> C2["409 with current grade"]
    Q -- yes --> Z{"Same score and feedback<br/>as Claude's?"}
    Z -- yes --> R1["status = reviewed"]
    Z -- no --> R2["status = overridden"]
    R1 --> OUT["version + 1, reviewed_by, reviewed_at, 200"]
    R2 --> OUT
```

---

## 6. Request path and authentication

The browser only ever talks to the Astro `web` service. It serves pages and proxies `/api/*` to FastAPI over
the private network, so the session cookie is same-origin and the API need not be public.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant W as web, Astro server
    participant A as api, FastAPI
    participant D as Postgres

    Note over B,W: Page request, for example GET /review/7
    B->>W: GET /review/7 with session cookie
    W->>A: GET /auth/me with the cookie
    alt no valid session
        A-->>W: 401
        W-->>B: 302 to /login
    else valid, path under /admin and role is not admin
        A-->>W: user
        W-->>B: 403
    else valid
        A-->>W: user
        W-->>B: HTML with the React island
    end

    Note over B,A: API call from the island, for example PATCH /api/grades/42
    B->>W: PATCH /api/grades/42 with Origin and JSON body
    W->>W: Astro origin check, form-like or content-type-less non-GET must match host
    W->>A: PATCH /grades/42, cookie and streamed body forwarded
    A->>A: verify JWT cookie, load active user
    A->>A: role check, TA scoping via visible_submissions
    A->>D: query or update
    D-->>A: rows
    A-->>W: JSON
    W-->>B: same status, headers and body
```

### Sign in with Google

Optional (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `PUBLIC_BASE_URL`). It only signs in users an admin already created.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant A as api (via web proxy)
    participant G as Google

    B->>A: GET /api/auth/google/login
    A-->>B: 302 to Google + oauth_tx cookie (state, nonce, PKCE verifier)
    B->>G: sign in, consent
    G-->>B: 302 /api/auth/google/callback?code&state
    B->>A: callback with oauth_tx cookie
    A->>A: state matches cookie?
    A->>G: exchange code + PKCE verifier + client secret
    G-->>A: id_token
    A->>A: verify signature, iss, aud, exp, nonce, email_verified
    alt active user with that email
        A-->>B: 302 / and session cookie
    else unknown, inactive or any check failed
        A-->>B: 302 /login?error=google_*
    end
```

Authentication facts:
- Login sets an **HttpOnly, SameSite=Lax** cookie `session` holding an HS256 JWT (`JWT_SECRET`, lifetime
  `JWT_EXPIRE_MINUTES`); `Secure` is added when `COOKIE_SECURE=true`.
- **Roles:** `admin` (everything) and `ta` (only submissions they are assigned to). Scoping lives in one
  place, `visible_submissions` in `routes/submissions.py`. Inaccessible items return **404**, not 403.
- **Password changes end sessions.** Each user has a `token_version`, copied into the token as the claim `v` when it
  is issued. A change or admin reset bumps it, so every older token is rejected with 401. The session that made the
  change gets a fresh token and carries on. Role, active state and deletion are checked from the database on every
  request, so they apply immediately too.
- **CSRF:** SameSite=Lax plus an origin check in the web middleware (`web/src/lib/csrf.ts`). A write must carry an
  `Origin` naming the host the server was addressed with, and that host must be in `ALLOWED_HOSTS`, which is read at
  runtime (see [how-it-runs.md](how-it-runs.md#allowed_hosts)). Form-like and content-type-less writes are held to
  this; JSON is not, because a cross-site page can't send it without a CORS preflight the server never approves.
  Body-less POSTs from the UI send `Content-Type: application/json` for that reason.

---

## 7. Claude login and readiness

```mermaid
flowchart TD
    S["docker compose up / podman-compose up"] --> H["claude container healthy<br/>liveness only: GET /health"]
    H --> L{"Credentials present in the<br/>claude-home volume, or API key,<br/>or OAuth token env var?"}
    L -- no --> N1["claude auth status: loggedIn false<br/>GET /grader/status: ready false<br/>Admin sees warning banner on Exams page"]
    N1 --> N2["Admin runs: claude-login.sh<br/>= claude auth login in the container"]
    N2 --> N3["Open printed URL in a browser,<br/>sign in, paste the code back"]
    N3 --> N4["Credentials stored in claude-home volume"]
    N4 --> OK
    N1 --> ALT["Alternative: claude-login.sh token,<br/>put CLAUDE_CODE_OAUTH_TOKEN in .env,<br/>or set ANTHROPIC_API_KEY"]
    ALT --> OK
    L -- yes --> OK["ready true<br/>papers grade normally"]
    N1 -. "papers imported meanwhile" .-> F["Fail with: Claude is not logged in.<br/>Run claude-login.sh"]
    F --> R["After login: Queue waiting and failed"]
    R --> OK
```

How readiness is computed: the API asks the provider (`GET /grader/status`, admin only) → the runner's
authenticated `GET /status` → `claude auth status --json`. It is *ready* if `loggedIn` is true **or**
`ANTHROPIC_API_KEY` is set in the container. A grading attempt without a login fails with a message that
names the fix instead of an empty error.

---

## 8. Restarts and recovery

What happens when the host, the container engine or a single container goes down and comes back. Data lives in
named volumes, so it is never in the container itself.

```mermaid
flowchart TD
    D["Container engine or host restarts,<br/>or a container crashes"] --> V{"Volumes intact?<br/>dbdata, storage, claude-home"}
    V -- "yes: normal restart, down, or recreate" --> P["restart: unless-stopped<br/>brings each service back<br/>Docker: automatic<br/>rootless podman after a reboot: needs linger and podman-restart.service"]
    V -- "no: down -v, or volume pruned" --> X["Data is gone: restore from backup"]

    P --> DB["db starts<br/>clean stop: normal start<br/>crash: replays its write-ahead log first"]
    P --> API["api starts<br/>retries the migration for about 60 s<br/>until the db accepts connections"]
    P --> WK["worker starts"]
    P --> CL["claude starts<br/>login is read from claude-home"]
    P --> WEB["web starts"]

    API --> H{"health check passes?"}
    H -- yes --> WEB
    H -- no --> API

    WK --> R1["recover_own: requeue jobs this worker<br/>had claimed before it stopped"]
    R1 --> R2["Worker threads resume claiming"]
    R2 --> R3["Every minute: recover_stale requeues jobs<br/>claimed by anyone but older than the timeout"]
    R3 --> R4["Interrupted papers are graded again<br/>one more model call per interrupted paper"]
```

What this guarantees and what it doesn't:
- **Committed data survives** every case above, including a hard kill of Postgres (verified by killing it mid-write:
  no acknowledged row was lost).
- **A paper that was being graded is graded again.** Nothing is half-saved: results are written in one transaction
  at the end. The interrupted model call is paid for twice.
- **Unsaved work in a browser is lost** (a grade edit that was typed but not saved, an upload in flight).
- Shutdown behaviour is tuned so restarts are fast: Postgres gets `SIGINT` (fast shutdown) and `web` runs under an
  init process so it obeys `SIGTERM`.
