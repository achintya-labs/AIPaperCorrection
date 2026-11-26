# API reference

40 endpoints on the FastAPI service, plus 3 internal endpoints on the Claude runner
([appendix](#appendix-claude-runner-internal)). The machine-readable spec is [`openapi.json`](openapi.json),
regenerated with `python scripts/export-openapi.py`. The running API also serves Swagger UI at `/docs`
(development port only).

- [Conventions](#conventions)
- [Auth](#auth) · [Exams and rubrics](#exams-and-rubrics) · [Students](#students) · [Submissions](#submissions)
  · [Import](#import) · [Grading control](#grading-control) · [Review](#review-and-export) · [Health](#health)

## Conventions

**Base URL.** Browsers use `/api/...` on the web service (`https://localhost:8443/api` through the HTTPS proxy, or `http://localhost:4321/api` on the host itself), which proxies to the API
with the prefix removed. For scripts in development the API is published on `http://127.0.0.1:8000`; the
examples below use that, with `API=http://127.0.0.1:8000`. In production only the web service should be
exposed, so use `https://your-host/api` and add two things the proxy's CSRF check wants on non-GET requests:
an `Origin: https://your-host` header and, for body-less POSTs, `Content-Type: application/json`.

**Authentication.** `POST /auth/login` sets an HttpOnly cookie named `session` (a signed JWT). Send it back on
every call. With curl keep a cookie jar:

```sh
API=http://127.0.0.1:8000
curl -s -c jar -H 'content-type: application/json' \
     -d '{"email":"admin@example.com","password":"…"}' $API/auth/login
curl -s -b jar $API/auth/me
```

**Roles.**

| Role | Can |
|---|---|
| `admin` | everything |
| `ta` | see exams, rubrics and papers **only for exams where a paper is assigned to them**, and review only those papers |

"any" in the tables below means any signed-in user. A TA asking for a submission or grade that is not theirs gets
**404** (not 403), the same as for one that does not exist.

**Errors.** Bodies are JSON.

| Status | Meaning | Body |
|---|---|---|
| 401 | Missing, invalid or expired session, or user deactivated | `{"detail": "Not authenticated"}` |
| 403 | Signed in but not allowed (admin-only endpoint) | `{"detail": "Admin only"}` |
| 404 | Not found, or not visible to you | `{"detail": "…"}` |
| 409 | State conflict (details per endpoint) | `{"detail": "…"}`, or for stale grade edits `{"detail": {"message": "…", "current": {…grade…}}}` |
| 413 | Upload too large | `{"detail": "name.pdf exceeds 100 MB"}` |
| 422 | Validation error | `{"detail": "…"}` for business rules, or FastAPI's list `{"detail": [{"loc": ["body","email"], "msg": "…", "type": "…"}]}` |

**Password rules.** Wherever a new password is set (create user, change, reset): **at least 8 characters and at most
72 bytes** (bcrypt ignores anything beyond 72 bytes, so longer passwords are rejected instead of silently truncated).
**Blank passwords.** An admin may leave the password **blank** when creating a user or resetting *another* user's
password. That account then has no password (`has_password: false` in `GET /auth/users`) and can sign in only with
Google. A blank password never signs anyone in, and nobody can blank their **own** password (422), through either
`PUT /auth/me/password` or by resetting themselves.

Violations are 422 with `{"detail": [{"loc": ["body", "new_password"], "msg": "Password must be at least 8 characters", …}]}`.

**Sessions and passwords.** Changing or resetting a password signs out that account's **other** sessions immediately
(their next request is 401). Role changes, deactivation and deletion also apply immediately.

**Types.** `SubmissionStatus` = `uploaded | queued | grading | ready_for_review | reviewed | failed`.
`GradeStatus` = `pending | reviewed | overridden`. `Role` = `admin | ta`. Confidences are numbers from 0 to 1.

---

## Auth

### `POST /auth/login`
Public. Body `{"email": str, "password": str}`. A blank `password` is always **401**, and so is any account that has no password (Google-only).
**200** → `UserOut` and `Set-Cookie: session=…; HttpOnly; SameSite=Lax` (`Secure` if `COOKIE_SECURE=true`; max-age =
`JWT_EXPIRE_MINUTES`). **401** wrong credentials or inactive user.

```json
{"id": 1, "email": "admin@example.com", "name": "Admin", "role": "admin"}
```

### `GET /auth/providers`
Public. **200** `{"google": bool}`: whether Google sign-in is configured (the login page shows its button only then).

### `GET /auth/google/login`
Public. Starts Google sign-in (authorization code + PKCE). **302** to Google and sets the short-lived `oauth_tx`
cookie holding `state`, `nonce` and the PKCE verifier. **404** if Google sign-in is not configured. Browsers reach it as
`/api/auth/google/login` (a plain link, not a fetch).

### `GET /auth/google/callback`
Public. Google's redirect target (`{PUBLIC_BASE_URL}/api/auth/google/callback`). Checks `state`, exchanges the code, verifies
the ID token (signature, issuer, audience, expiry, nonce, `email_verified`) and signs in the **existing, active** user with
that email (case-insensitive); accounts are never created from Google. **302** to `/` with the `session` cookie on success;
**302** to `/login?error=<code>` otherwise, where code is `google_not_allowed` (no matching active user),
`google_cancelled` or `google_failed`. **404** if not configured.

### `POST /auth/logout`
Public. **204**, clears the cookie.

### `GET /auth/me`
Any. **200** → `UserOut`. **401** if not signed in.

### `PUT /auth/me/password`
Any. **Change your own password.** Body:

| Field | Notes |
|---|---|
| `current_password` | Required, must be correct |
| `new_password` | Must follow the [password rules](#conventions) and differ from the current one |

**204**, with a fresh `Set-Cookie: session=…`: the session you made the change from stays signed in, while **every
other session of yours is signed out**.

| Status | When |
|---|---|
| 400 | `current_password` is wrong. This is deliberately **not 401**, which the web client treats as "session expired" and would redirect to the login page |
| 401 | Not signed in |
| 422 | New password breaks the rules, or equals the current one |

```sh
curl -s -b jar -c jar -X PUT -H 'content-type: application/json' \
     -d '{"current_password":"old-password-123","new_password":"new-password-456"}' $API/auth/me/password
```

### `GET /auth/users`
Admin. **200** → `UserAdminOut[]` ordered by id: a `UserOut` plus the account's state and activity, so you can make
an informed choice before deleting.

```json
{"id": 2, "email": "ta1@school.edu", "name": "Tess", "role": "ta",
 "active": true, "assigned_papers": 12, "reviewed_grades": 31}
```

### `POST /auth/users`
Admin. Body: `email` (required), `password` (required; may be `""` for a Google-only account), `name` (default `""`), `role` (`ta` default, or `admin`).
**201** → `UserOut`. **409** email exists. **422** if the password breaks the [password rules](#conventions).

```sh
curl -s -b jar -H 'content-type: application/json' \
     -d '{"email":"ta1@school.edu","name":"Tess","password":"a-long-password"}' $API/auth/users
```

### `PATCH /auth/users/{user_id}`
Admin. Change a user's **role**, **active** flag and/or **name**. Body: `{"role": "admin" | "ta"}`, `{"active": bool}`
and/or `{"name": str}`; send at least one. The email is the user's key and can't be changed. **200** → `UserAdminOut`.

The change takes effect **immediately**, even for someone who is signed in: the session cookie holds only the user
id, and role and active state are read from the database on every request. A deactivated user's next request gets
401 and they cannot sign in until reactivated. Deactivating keeps their paper assignments and review history.

| Status | When |
|---|---|
| 404 | No such user |
| 409 | You tried to demote or deactivate **yourself** (`"You can't demote or deactivate yourself; ask another admin"`) |
| 409 | It would leave no active admin (`"At least one active admin is required"`) |
| 422 | Neither field given, or an invalid role |

```sh
curl -s -b jar -X PATCH -H 'content-type: application/json' -d '{"role":"admin"}' $API/auth/users/3
curl -s -b jar -X PATCH -H 'content-type: application/json' -d '{"active":false}' $API/auth/users/3
```

### `PUT /auth/users/{user_id}/password`
Admin. **Reset a user's password** directly without requiring the current password. Body `{"password": str}`
following the [password rules](#conventions), or `""` to remove the user's password (Google-only). **204**. **422** for `""` on your own account. All other sessions of that user are signed out (if resetting
your own account, your current session stays signed in). Works for inactive users and does not reactivate them.

| Status | When |
|---|---|
| 404 | No such user |
| 422 | Password breaks the rules |

```sh
curl -s -b jar -X PUT -H 'content-type: application/json' -d '{"password":"temporary-pass-9"}' $API/auth/users/3/password
```

### `DELETE /auth/users/{user_id}`
Admin. Permanently deletes a user. Query: `force` (bool, default `false`). **204**.

What happens to what they touched:

| Their data | Result |
|---|---|
| Paper assignments | Removed; those papers become unassigned (the papers themselves are never deleted) |
| Grades they reviewed | The grades are **kept** (scores, feedback, status), but `reviewed_by` is cleared. Because that loses attribution, the delete is **refused (409)** unless `force=true`. To keep the history, deactivate instead |
| Their session | Invalid immediately (401) |

| Status | When |
|---|---|
| 404 | No such user |
| 409 | You tried to delete **yourself**, or the last active admin |
| 409 | The user reviewed grades and `force` is not set. The message gives the count and the alternatives |

```sh
curl -s -b jar -X DELETE $API/auth/users/3                 # refused if they reviewed grades
curl -s -b jar -X DELETE "$API/auth/users/3?force=true"    # clears the reviewer on those grades
```

---

## Exams and rubrics

### `GET /exams`
Any, **scoped**: admins get every exam; a TA gets only exams in which at least one paper is assigned to them (so their
exam selector never lists exams they don't grade). The list follows assignment changes immediately.
**200** → `ExamOut[]`: `{"id": 1, "name": "Midterm", "model_override": null}`.

### `POST /exams`
Admin. Body `{"name": str, "model_override": str | null}`. **201** → `ExamOut`. `model_override` is a model id used
instead of `CLAUDE_MODEL` for this exam's papers (applies to papers graded from then on).

### `PATCH /exams/{exam_id}`
Admin. Body is the same as create and **replaces both fields** (send `name` every time; omitting `model_override`
sets it to `null`). **200** → `ExamOut`. **404**.

### `POST /exams/{exam_id}/rubrics`
Admin. Creates a **new rubric version** (rubrics are never edited in place; version = previous + 1).
Body:

```json
{"questions": [
  {"number": "1", "text": "Average speed?", "max_points": 2, "expected_answer": "5 m/s",
   "criteria": [{"description": "correct formula", "points": 1}, {"description": "correct result", "points": 1}]},
  {"number": "2a", "max_points": 3}
]}
```

`questions` needs at least one; `number` is a string and unique within the rubric; `max_points` > 0; each criterion
`points` may be negative (a deduction), and the **positive** criteria points of a question must sum to at most its `max_points`.
**201** → `RubricOut` `{id, exam_id, version, questions[{id, number, text, max_points, expected_answer, criteria}]}`.
**404** exam. **422** duplicate numbers, criteria over max, or schema errors.

### `GET /exams/{exam_id}/rubric`
Any, scoped like `GET /exams`. **200** → the **latest** `RubricOut`. **404** if the exam has no rubric, and also, for a
TA, if none of its papers are assigned to them (the rubric contains the model answers, and an unassigned exam looks
exactly like a missing one).

### `DELETE /exams/{exam_id}`
Admin. **Permanently deletes an exam**: every rubric version and every paper in it, with their grades, page images,
grading history, assignments and stored PDFs. **204**. Students are kept (they may have papers in other exams).
Query: `force` (bool, default `false`).

| Status | When |
|---|---|
| 404 | No such exam |
| 409 | The exam has papers and `force` is not set. The message gives the paper count and how many TA-reviewed grades they hold, so you know what a forced delete would discard |
| 409 | A paper in the exam is being graded right now. Wait for it to finish (a stuck job is requeued automatically after a while) |

An exam with no papers deletes without `force`. The web UI asks for confirmation and, when papers exist, makes you type
the exam's name.

```sh
curl -s -b jar -X DELETE $API/exams/3                  # refused if it has papers
curl -s -b jar -X DELETE "$API/exams/3?force=true"     # deletes the exam and all its papers
```

---

## Students

Students are created automatically by import; these endpoints are for managing them directly.

### `POST /students`
Admin. Body is an **array**: `[{"external_id": "1001", "name": "Ann Lee"}]`. Upserts by `external_id` (an existing
student's name is only updated if you send one). **201** → `StudentOut[]` `{id, external_id, name}`.

### `GET /students`
Admin. **200** → `StudentOut[]` ordered by `external_id`.

### `PATCH /students/{student_id}`
Admin. Corrects a student's roll number and/or name. Body `{"external_id": "EE24B001", "name": "Ann Lee"}`; send at
least one. **200** → `StudentOut`. **404** no such student. **409** another student already has that roll number.
**422** nothing to change, or an empty roll number.

---

## Submissions

A submission is one student's paper for one exam.

### `GET /submissions`
Any, **scoped**: admins see all, TAs only submissions they are assigned to. Query: `exam_id` (int), `status`
(`SubmissionStatus`), both optional. **200** → `SubmissionOut[]` ordered by id:

```json
{"id": 7, "exam_id": 1,
 "student": {"id": 3, "external_id": "1001", "name": "Ann Lee"},
 "ta_ids": [2, 5], "status": "ready_for_review",
 "overall_confidence": 0.82,
 "grades_total": 4, "grades_reviewed": 1, "min_confidence": 0.55}
```

`grades_total`/`grades_reviewed` count grades and those no longer `pending`; `min_confidence` is the lowest
per-question AI confidence. These three are only filled in by this list endpoint (they are `0`/`null` elsewhere).

### `GET /submissions/{submission_id}`
Any, scoped. **200** → `SubmissionOut`. **404** if missing or not yours.

### `PUT /submissions/{submission_id}/assignment`
Admin. Body `{"ta_ids": [2, 5]}`: **replaces** the set of assigned TAs (several are allowed; `[]` unassigns
everyone). **200** → `SubmissionOut`. **404** submission. **422** if any id is unknown or inactive.

### `PUT /submissions/bulk/assignment`
Admin. Assigns TAs to several papers at once. Body `{"submission_ids": [3, 4], "ta_ids": [2, 5], "mode": "replace"}`.
`mode` is `replace` (default: each paper's TAs become exactly `ta_ids`; `[]` unassigns), `add` (keep the current TAs and
add these) or `remove` (take these off). **200** → `{"updated": 2}`. **404** if any submission is unknown.
**422** unknown/inactive TA, or `add`/`remove` with no TAs.

### `POST /submissions/bulk/delete`
Admin. Permanently deletes several papers (what `DELETE /submissions/{id}` does for one). Body
`{"submission_ids": [3, 4], "force": false}`. All or nothing. **200** → `{"deleted": 2}`. **404** if any id is unknown.
**409** if any is being graded, or any holds TA-reviewed grades and `force` is false.

### `DELETE /submissions/{submission_id}`
Admin. **Permanently deletes one paper**: its grades, page images, grading history and assignments, and the stored PDF.
The student record stays. **204**. Query: `force` (bool, default `false`).

| Status | When |
|---|---|
| 404 | No such submission |
| 409 | The paper has TA-reviewed grades and `force` is not set. Deleting it discards that work |
| 409 | The paper is being graded right now. This is refused even with `force`, because the worker would otherwise write results for a paper that no longer exists |

```sh
curl -s -b jar -X DELETE $API/submissions/7
curl -s -b jar -X DELETE "$API/submissions/7?force=true"
```

---

## Import

### `POST /exams/{exam_id}/imports`
Admin. `multipart/form-data`. Two layouts: **one PDF per student** named `<student_id>.pdf`, or **one combined
PDF** with page ranges in the CSV. Preview with `dry_run=true`, then repeat with `dry_run=false`.
Flow diagram: [workflows.md#3](workflows.md#3-import-decision-flow).

| Field | Type | Notes |
|---|---|---|
| `files` | file, repeated | Required. One PDF per student, or exactly one combined PDF |
| `mapping_csv` | file | Optional unless using page ranges. UTF-8, header row |
| `assign` | `csv` \| `round_robin` \| `none` | Default: `csv` if the CSV has any `ta_emails`, otherwise `none` |
| `reviewers_per_paper` | int ≥ 1, default 1 | `round_robin` only; capped at the number of active TAs |
| `dry_run` | bool, default false | `true` validates and previews, saves nothing |

**CSV columns:** `student_id` (required), `name`, `ta_emails` (several separated by `;`; must be active TAs),
and optionally `first_page` + `last_page` (1-based, inclusive; both or neither). Any row with page ranges makes
it a combined import; pages outside every range are skipped, ranges may not overlap or exceed the PDF.

```sh
curl -s -b jar -F files=@1001.pdf -F files=@1002.pdf -F mapping_csv=@map.csv \
     -F assign=round_robin -F reviewers_per_paper=2 -F dry_run=true $API/exams/1/imports
```

**200** → `ImportResult`:

```json
{"dry_run": true, "created": 0, "items": [
  {"student_external_id": "1001", "filename": "1001.pdf", "pages": 6, "status": "ok",
   "detail": "", "ta_emails": ["ta1@school.edu", "ta2@school.edu"], "submission_id": null},
  {"student_external_id": "1002", "filename": "1002.pdf", "pages": null, "status": "error",
   "detail": "Not a readable PDF: …", "ta_emails": [], "submission_id": null}
]}
```

Per item, `status` is `ok`, `conflict` (that student already has a submission for this exam) or `error`; only
`ok` items are saved, the rest are reported and skipped. With `dry_run=false`, `created` is the number saved and
each saved item carries its `submission_id`. New submissions are always `uploaded`; grading is started
separately (see [Grading control](#grading-control)).

**404** exam. **413** a file over `MAX_UPLOAD_MB`. **422** request-level problems: bad CSV, duplicate
`student_id`, bad or overlapping page ranges, combined import without exactly one PDF, round-robin with no
active TAs.

### `POST /exams/{exam_id}/imports/auto-split`
Admin. `multipart/form-data` with `file` (one combined PDF) and an optional **roster** `roll_numbers_csv` that limits
Claude to the roll numbers on it. Stages the PDF and queues a background job for the worker (Claude) to propose paper
boundaries (`first_page`, `last_page`) and extract student roll numbers (`student_external_id`) and names. Claude is shown
the top of every page tiled onto a few contact-sheet images, in one request
([how it works](workflows.md#auto-split-of-a-combined-pdf)), so even a 150-page scan takes about half a minute.

**Roster CSV format** (UTF-8, comma-separated; Excel's "CSV UTF-8" is fine):

| Layout | Example |
|---|---|
| one column: roll numbers | `2024CS01` ⏎ `2024CS02` |
| two columns: roll number, name | `2024CS01,Ann Lee` ⏎ `2024CS02,Bo Chen` |

A header row is optional: `roll_number` (also `roll`, `roll_no`, `student_id`, `id`, `registration_number`, `reg_no`), then `name`
(also `student_name`, `full_name`). Blank lines are skipped, values are trimmed, a name may be left empty for some rows, and
quoted values may contain commas. The roster's names help Claude match a paper and are **authoritative afterwards**: a split
whose roll number is on the roster gets the roster's name, and a split with no readable roll number whose name matches exactly
one roster name gets that name's roll number.

Returns `AutoSplitResult` (`split_token`, `filename`, `page_count`, `status`, `error`, `allowed_roll_numbers`, `roll_names`
(roll number → name, only for rows that had one), `splits`, `existing_student_ids`). **404** exam. **413** file over
`MAX_UPLOAD_MB`. **422** invalid PDF, or a roster that fails the format check; the message names the line, for example
`Roster CSV: line 3: roll number '2024CS01' appears twice (first on line 1)`. Checked: empty file, not UTF-8, more than two columns,
an empty roll number, a duplicate roll number, a second-column header that is not a name column, and `;` or tab separators.

### `GET /exams/{exam_id}/imports/auto-split`
Admin. Lists staged/background auto-split jobs for the exam (`AutoSplitResult[]`, newest first), so the UI can
resume in-progress or completed splits after leaving the page. **404** exam.

### `GET /exams/{exam_id}/imports/auto-split/{split_token}`
Admin. Polls the status (`queued | processing | ready | failed`) and proposed splits of one auto-split job. **404** missing.

### `DELETE /exams/{exam_id}/imports/auto-split/{split_token}`
Admin. Discards a staged auto-split job and deletes its temporary PDF. **204**. **404** missing.

### `POST /exams/{exam_id}/imports/auto-split/{split_token}/retry`
Admin. Requeues a failed (or ready) auto-split job using the already-staged PDF. **200** → `AutoSplitResult`. **404** missing. **409** if already `queued` or `processing`.

### `GET /exams/{exam_id}/imports/auto-split/{split_token}/pages/{index}/thumb`
Admin. A small preview of 1-based page `index` for the strip of page thumbnails: a **JPEG** rendered at 30 dpi (about
15 KB, against about 770 KB for the full-size `/image`). Kept in server memory after the first request and cached by the
browser for a day (`Cache-Control: private, max-age=86400`). **404** missing token or page out of range. Click-to-zoom uses
`/image`.

### `GET /exams/{exam_id}/imports/auto-split/{split_token}/pages/{index}/image`
Admin. Returns a PNG preview (`image/png`) of 1-based page `index` from the staged auto-split PDF so the admin
can visually verify the proposed split and roll numbers. **404** invalid token, missing session, or out-of-range page.

### `POST /exams/{exam_id}/imports/auto-split/{split_token}/commit`
Admin. Body `{"items": [{"student_external_id": str, "student_name": str, "first_page": int, "last_page": int, "ta_ids": [int]}]}`.
Splits the staged PDF into per-student submissions, assigns the specified TAs, deletes the staged PDF, and
returns `ImportResult`. **404** exam or split session. **422** empty/duplicate `student_external_id`, invalid/overlapping page ranges, or unknown/inactive TA.

---

## Grading control

All admin-only.

Nothing is graded automatically: imported papers stay `uploaded` until an admin starts grading, for one paper
(`regrade`), a selection (`bulk/grade`) or the whole exam (`queue`).

### `POST /exams/{exam_id}/queue`
"Grade everything": queues every `uploaded` or `failed` submission of the exam. **200** → `{"queued": 3}`. **404** exam. **409** if the exam has no rubric.

### `POST /submissions/{submission_id}/regrade`
Sends a finished or failed submission back to the queue; Claude's grades are replaced when it completes.
Body (send `{}` for defaults):

| Field | Default | Meaning |
|---|---|---|
| `rubric_version` | `null` | A specific version; `null` = latest at the time the job runs |
| `force` | `false` | Allow discarding grades a TA already reviewed or edited |

Also starts grading an `uploaded` paper for the first time.

**200** → `{"queued": 1}`. **404** submission. **409** no rubric, already `queued`/`grading`, or TA-reviewed grades
exist and `force` is false. **422** unknown `rubric_version`.

### `POST /submissions/bulk/grade`
Starts grading (or regrading) the chosen papers with the latest rubric. Body `{"submission_ids": [3, 4, 9], "force": false}`.
Papers already `queued`/`grading` are skipped, as are papers with TA-reviewed grades unless `force` is true.
**200** → `{"queued": 2, "skipped": 1}`. **404** if any id is unknown. **409** if an exam involved has no rubric.

### `GET /grader/status`
Is the grading backend usable? **200** always:

```json
{"provider": "claude_runner", "ready": false, "auth_method": "none", "detail": "Not logged in"}
```

On misconfiguration (e.g. missing runner token) it returns `{"ready": false, "detail": "…"}`.

---

## Review and export

### `GET /submissions/{submission_id}/detail`
Any, scoped. Everything the review screen needs. **200** → `SubmissionDetail` (a `SubmissionOut` plus `error`,
`pages`, `grades`). Grades are sorted by question number (numerically, so `2` before `10`, `2a` after `2`).

```json
{"id": 7, "status": "ready_for_review", "error": null, "overall_confidence": 0.82, "...": "…",
 "pages": [
   {"index": 1, "question_numbers": ["1"], "transcription": "Q1. Speed = 100 m / 20 s = 5 m/s", "confidence": 0.95},
   {"index": 2, "question_numbers": ["2"], "transcription": "…", "confidence": 0.95}],
 "grades": [
   {"id": 42, "question_id": 11, "question_number": "1", "question_text": "Average speed?",
    "expected_answer": "5 m/s", "criteria": [{"description": "correct result", "points": 1}], "max_points": 2,
    "ai_score": 2, "ai_feedback": "Correct.", "ai_note": "", "ai_confidence": 0.95,
    "final_score": null, "final_feedback": null, "status": "pending", "version": 1, "reviewed_by": null}]}
```

`ai_*` fields never change after grading; `final_*` are the TA's values (`null` until reviewed).

### `GET /submissions/{submission_id}/pages/{index}/image`
Any, scoped. `index` is 1-based. **200** → `image/png` (`Cache-Control: private, max-age=3600`). **404** page or
submission not found/not yours.

### `PATCH /grades/{grade_id}`
Any, scoped. Save a TA's decision on one question. Body:

| Field | Notes |
|---|---|
| `version` | The `version` you read. Required |
| `final_score` | Number from `-max_points` to `max_points` (negative marking is allowed) |
| `final_feedback` | Text |

`ai_note` is Claude's remark to the reviewing TA (unclear handwriting, an accepted alternative method, …). It is shown in the
review screen only: it is not part of the student-facing feedback and is left out of the CSV and PDF exports.

**Approving** Claude's grade = send its `ai_score` and `ai_feedback` unchanged → `status: "reviewed"`. Any other
values → `"overridden"`. On success `version` increases by 1 and `reviewed_by`/`reviewed_at` are set.
**200** → `GradeOut`.

| Status | When |
|---|---|
| 404 | Grade missing or its submission is not visible to you |
| 409 | Submission is not `ready_for_review`/`reviewed` (`detail` is a string) |
| 409 | **Stale version**: someone saved first. `detail` is `{"message": …, "current": <GradeOut>}`; re-send with `current.version` |
| 422 | Score above `max_points` or below `-max_points` |

Sequence diagram: [workflows.md#5](workflows.md#5-review-and-edit-conflicts).

### `POST /submissions/{submission_id}/complete`
Any, scoped. Marks the paper `reviewed`. Idempotent. **200** → `SubmissionOut`. **404**. **409** if the submission
is not reviewable, has no grades, or any grade is still `pending` (`"2 grade(s) still need review"`).

### `GET /exams/{exam_id}/export.csv`
Admin. `text/csv` download (`Content-Disposition: attachment; filename="exam-{id}-grades.csv"`).
Columns, in order: `student_id, name`, one `Q<number>` score column per question (latest rubric), `total, status,
graders`, then one `Q<number> feedback` column per question. Scores and totals come first so their positions are
stable for spreadsheet formulas. A question's score and its feedback each use the TA's `final_*` value when set,
otherwise Claude's `ai_*` value; the paper's `status` tells you whether it was reviewed. Feedback is free text, so it
can contain commas, quotes and line breaks (the file is properly quoted). Text cells (names, ids, feedback) that start
with `=`, `+`, `-` or `@` are prefixed with `'` to prevent spreadsheet formula injection.
**404** exam.

```sh
curl -s -b jar -o grades.csv $API/exams/1/export.csv
```

### `GET /submissions/{submission_id}/raw.pdf`
Any, scoped. The **original scan** exactly as uploaded (`application/pdf`), whether or not the paper is graded.
**404** if missing, not yours, or PDF file not found.

### `GET /submissions/{submission_id}/export.pdf`
Any, scoped. The **graded** PDF. If the paper is not graded yet (`uploaded`, `queued`, `grading` or `failed`) it returns
the **original scan** instead, same as `raw.pdf`. Once graded (`ready_for_review` or `reviewed`) it downloads the PDF (`application/pdf`) with each page extended to the right so that
question scores and comments (`final_*` when reviewed/overridden, otherwise `ai_*`) appear beside the pages where
those questions are answered without obscuring the original answer sheet. Any question not matched to a page is
placed on the last page. A **marks summary page** is added in front, the same size as the first page: exam name, student,
total marks obtained out of the maximum, and a breakdown per section (the part of the question number before the first
`.`, so `II.3` is in Section II; numbers without a dot are grouped as one block). **404** if missing, not yours, or PDF file not found.

```sh
curl -s -b jar -o paper.pdf $API/submissions/7/export.pdf
```

### `GET /exams/{exam_id}/export.zip`
Admin. Downloads a ZIP archive (`application/zip`, `Content-Disposition: attachment; filename="exam-{id}-papers.zip"`)
containing one PDF (`<student_id>.pdf`) per submission in the exam: graded papers annotated with the marks summary first
page, papers not graded yet as the original scan (the same rule as `export.pdf`). **404** exam.

Query (both optional):

| Param | Meaning |
|---|---|
| `ids` | Comma-separated submission ids: export only those papers (used by the web UI's selection) |
| `raw` | `true`: the **original scans** of every paper (or of `ids`), graded or not: no marks, no summary page |

Bad `ids` → **422**.

```sh
curl -s -b jar -o papers.zip $API/exams/1/export.zip
```

---

## Health

### `GET /health`
Public. **200** `{"status": "ok"}`. Liveness only.

---

## Appendix: Claude runner (internal)

Served by the `claude` container on port 9000, **internal network only**, called by the worker and the API. It
has no OpenAPI/Swagger. Auth is `Authorization: Bearer <CLAUDE_RUNNER_TOKEN>`; it refuses to start without a token.

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Liveness |
| `GET /status` | bearer | `{"ready": bool, "auth_method": …, "detail": …}` from `claude auth status` |
| `POST /run` | bearer | Run `claude -p` on the supplied pages and prompt |

`POST /run` (`multipart/form-data`):

| Field | Rules |
|---|---|
| `model` | `[A-Za-z0-9._:\-\[\]]{1,100}` |
| `prompt` | ≤ 50 000 characters; sent on stdin |
| `timeout` | seconds, 1-3600, default 600 |
| `files` | up to 400, **names must be `page-<n>.png` or `rubric.json`**, unique; total ≤ `CLAUDE_RUNNER_MAX_REQUEST_MB` |

Callers cannot set CLI flags; they are fixed server-side to a read-only configuration. Responses:
**200** `{"returncode": int, "stdout": str, "stderr": str}` (the CLI's own exit status is in `returncode`);
**401** bad token; **413** too large; **422** invalid model or file names; **502** the CLI could not be run;
**504** timed out.
