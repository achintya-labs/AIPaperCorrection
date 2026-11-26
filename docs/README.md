# Paper Grader documentation

TAs verify AI-graded, scanned answer sheets. An admin writes a rubric and imports PDFs; Claude (through the
Claude Code CLI) transcribes each page, finds which questions are on it, grades every question with feedback
and reports a confidence; TAs approve or edit. Claude's output is kept untouched alongside the TA's decision.

```
browser ─► caddy (WAF) ─► tinyauth (login) ─► web (Astro) ─► api (FastAPI) ─► db (Postgres)
                              │                                   │              ▲
                         lldap (users, groups)         worker ────┘──► claude container ─► Claude Code CLI ─► Anthropic
```

## Read in this order

| Document | Read it to… |
|---|---|
| [how-it-runs.md](how-it-runs.md) | start the system, single sign-on and LDAP users, configure it, log Claude in, operate, back up, troubleshoot |
| [architecture.md](architecture.md) | understand the components, data model, security boundaries and why things are built this way |
| [workflows.md](workflows.md) | see the flowcharts: lifecycle, submission states, import, grading pipeline, review conflicts, auth path, Claude login, restarts, gateway sign-in, people and roles, user migration |
| [api.md](api.md) | call the API: conventions, every endpoint, request/response shapes, error cases, curl examples |
| [openapi.json](openapi.json) | machine-readable API spec (generated) |
| [development.md](development.md) | set up the repo-local tooling, run tests, understand the commit gate, CI and how images are published |
| [libraries.md](libraries.md) | find standard libraries that could replace hand-written code or reduce risk |

## Keeping the docs honest

- `docs/openapi.json` is generated: `python scripts/export-openapi.py` (add `--check` in CI).
- `api/tests/test_docs.py` fails if an endpoint is missing from `api.md` or if `openapi.json` is stale.
- Diagrams are Mermaid text in the Markdown files; edit them in place.
