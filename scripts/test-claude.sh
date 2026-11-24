#!/usr/bin/env bash
# Smoke-test the live Claude runner from inside the running `api` container:
#   1. Checks runner/login readiness
#   2. Runs a 1-page auto-split call (uses CLAUDE_SPLIT_MODEL or CLAUDE_MODEL)
#   3. Runs a 1-page grading call (uses CLAUDE_MODEL)
set -euo pipefail
cd "$(dirname "$0")/.."

./scripts/compose.sh exec -T api python - <<'PY'
import sys, time
import fitz
from app.config import get_settings
from app.grader import get_provider
from app.grader.base import PageImage, QuestionSpec, RubricSpec

s = get_settings()
p = get_provider(s)
print(f"Provider: {p.name}")

st = p.status()
print(f"Status:   {st}")
if not st.get("ready"):
    print("FAIL: Claude is not ready. Run ./scripts/claude-login.sh or check ANTHROPIC_API_KEY.", file=sys.stderr)
    sys.exit(1)

doc = fitz.open()
page = doc.new_page(width=400, height=200)
page.insert_text((30, 50), "Roll No: 1001\nName: Test Student\nQ1: 2 + 2 = 4", fontsize=14)
png = page.get_pixmap(dpi=100).tobytes("png")
pages = [PageImage(index=1, png=png)]

split_model = s.claude_split_model or s.claude_model
t0 = time.monotonic()
split = p.propose_split(pages, split_model)
print(f"Split OK  ({time.monotonic() - t0:.1f}s, model={split_model}): {split.model_dump()}")

rubric = RubricSpec(questions=[QuestionSpec(number="1", text="What is 2 + 2?", max_points=2, expected_answer="4")])
t0 = time.monotonic()
outcome = p.grade(pages, rubric, s.claude_model)
q = outcome.result.questions[0]
print(f"Grade OK  ({time.monotonic() - t0:.1f}s, model={s.claude_model}): Q{q.question_number}={q.score}/{rubric.questions[0].max_points} ({q.feedback})")
PY
