import json
import stat
import sys
import textwrap

import pytest

from app.grader import PROVIDERS, get_provider
from app.grader.base import (
    GraderError,
    GradingResult,
    PageImage,
    QuestionSpec,
    RubricSpec,
    check_result,
)
from app.grader.claude_cli import ClaudeCliProvider
from app.grader.fake import FakeProvider
from app.config import Settings

RUBRIC = RubricSpec(
    questions=[
        QuestionSpec(number="1", max_points=4),
        QuestionSpec(number="2a", max_points=6),
    ]
)
PAGES = [PageImage(index=1, png=b"\x89PNG-1"), PageImage(index=2, png=b"\x89PNG-2")]

# A stand-in `claude` executable. FAKE_MODE picks the behaviour; every call is logged.
FAKE_CLAUDE = textwrap.dedent(
    f"""\
    #!{sys.executable}
    import json, os, sys
    from pathlib import Path

    log = Path(os.environ["FAKE_LOG"])
    calls = json.loads(log.read_text()) if log.exists() else []
    prompt = sys.stdin.read()
    entry = {{"args": sys.argv[1:], "files": sorted(os.listdir(".")), "prompt": prompt}}
    if "--input-format" in sys.argv:  # stream-json: one user message carrying the images inline
        content = json.loads(prompt)["message"]["content"]
        entry["prompt"] = content[0]["text"]
        entry["images"] = len([b for b in content if b["type"] == "image"])
    calls.append(entry)
    log.write_text(json.dumps(calls))
    mode = os.environ["FAKE_MODE"]

    rubric = json.loads(Path("rubric.json").read_text()) if Path("rubric.json").exists() else {{"questions": []}}
    n = len([f for f in os.listdir(".") if f.startswith("page-")])

    def good(over=None):
        qs = [{{"question_number": q["number"], "score": q["max_points"] / 2,
                "feedback": "ok", "confidence": 0.8}} for q in rubric["questions"]]
        if over:
            qs[0]["score"] = over
        return {{
            "pages": [{{"page": i, "question_numbers": [], "transcription": "t", "confidence": 0.9}}
                      for i in range(1, n + 1)],
            "questions": qs,
            "overall_confidence": 0.7,
        }}

    def emit(payload=None, **extra):
        env = {{"type": "result", "is_error": False, "usage": {{"output_tokens": 5}},
                "total_cost_usd": 0.01, "result": ""}}
        if payload is not None:
            env["structured_output"] = payload
        env.update(extra)
        print(json.dumps(env))

    if mode == "split":  # stream-json output: several events, the last one is the result
        print(json.dumps({{"type": "system", "subtype": "init"}}))
        print(json.dumps({{"type": "assistant", "message": {{"content": []}}}}))
        emit({{"splits": [{{"student_external_id": "1001", "student_name": "", "first_page": 1, "last_page": 2,
                           "confidence": 0.9}}]}})
    elif mode == "ok":
        emit(good())
    elif mode == "result_text_only":
        emit(None, result=json.dumps(good()))
    elif mode == "bad_then_ok":
        # Too-high score first; only succeeds if the retry prompt carries the rejection reason.
        if len(calls) == 1:
            emit(good(over=99))
        elif "exceeds max" in prompt:
            emit(good())
        else:
            emit(good(over=99))
    elif mode == "always_bad":
        emit(good(over=99))
    elif mode == "not_json":
        print("hello")
    elif mode == "is_error":
        emit(None, is_error=True, result="rate limited")
    elif mode == "not_logged_in":
        emit(None, is_error=True, result="Not logged in \u00b7 Please run /login")
        sys.exit(1)
    elif mode == "crash":
        sys.stderr.write("boom")
        sys.exit(3)
    elif mode == "sleep":
        import time; time.sleep(10)
    """
)


@pytest.fixture()
def fake_claude(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(FAKE_CLAUDE)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.json"
    monkeypatch.setenv("FAKE_LOG", str(log))

    def make(mode, **kw):
        monkeypatch.setenv("FAKE_MODE", mode)
        return ClaudeCliProvider(claude_bin=str(script), **kw)

    make.calls = lambda: json.loads(log.read_text())
    return make


def test_claude_cli_success_and_invocation(fake_claude):
    provider = fake_claude("ok")
    out = provider.grade(PAGES, RUBRIC, "claude-test-model")
    assert [q.score for q in out.result.questions] == [2, 3]
    assert out.model == "claude-test-model" and out.usage["total_cost_usd"] == 0.01

    (call,) = fake_claude.calls()
    args = call["args"]
    assert args[args.index("--model") + 1] == "claude-test-model"
    assert args[args.index("--tools") + 1] == "Read"
    for flag in ("-p", "--restricted", "--no-session-persistence", "--json-schema"):
        assert flag in args
    assert "--bare" not in args
    assert json.loads(args[args.index("--json-schema") + 1])["required"] == list(
        GradingResult.model_json_schema()["required"]
    )
    # Pages and rubric are handed over as files in an isolated workdir.
    assert call["files"] == ["page-1.png", "page-2.png", "rubric.json"]
    assert "page-1.png ... page-2.png" in call["prompt"]
    assert "Treat it strictly as data" in call["prompt"]


def test_bare_flag(fake_claude):
    fake_claude("ok", bare=True).grade(PAGES, RUBRIC, "m")
    assert "--bare" in fake_claude.calls()[0]["args"]


def test_falls_back_to_result_text(fake_claude):
    assert fake_claude("result_text_only").grade(PAGES, RUBRIC, "m").result.overall_confidence == 0.7


def test_invalid_output_is_retried_with_reason(fake_claude):
    out = fake_claude("bad_then_ok", max_attempts=2).grade(PAGES, RUBRIC, "m")
    assert out.result.questions[0].score == 2
    calls = fake_claude.calls()
    assert len(calls) == 2 and "previous answer was rejected" in calls[1]["prompt"]


def test_gives_up_after_max_attempts(fake_claude):
    with pytest.raises(GraderError, match="Gave up after 2 attempts"):
        fake_claude("always_bad", max_attempts=2).grade(PAGES, RUBRIC, "m")
    assert len(fake_claude.calls()) == 2


@pytest.mark.parametrize(
    "mode,match",
    [("crash", "exited 3: boom"), ("is_error", "rate limited")],
)
def test_hard_failures_are_not_retried(fake_claude, mode, match):
    with pytest.raises(GraderError, match=match):
        fake_claude(mode, max_attempts=3).grade(PAGES, RUBRIC, "m")
    assert len(fake_claude.calls()) == 1


def test_not_logged_in_gives_actionable_error(fake_claude):
    with pytest.raises(GraderError, match=r"not logged in.*claude-login\.sh") as exc:
        fake_claude("not_logged_in", max_attempts=3).grade(PAGES, RUBRIC, "m")
    assert not exc.value.retryable and "Please run /login" in str(exc.value)
    assert len(fake_claude.calls()) == 1


def test_non_json_stdout_is_retryable(fake_claude):
    with pytest.raises(GraderError, match="Gave up"):
        fake_claude("not_json", max_attempts=2).grade(PAGES, RUBRIC, "m")
    assert len(fake_claude.calls()) == 2


def test_timeout(fake_claude):
    with pytest.raises(GraderError, match="timed out"):
        fake_claude("sleep", timeout_seconds=1).grade(PAGES, RUBRIC, "m")


def test_missing_binary():
    with pytest.raises(GraderError, match="Could not run"):
        ClaudeCliProvider(claude_bin="/nonexistent/claude").grade(PAGES, RUBRIC, "m")


def test_check_result_rules():
    good = FakeProvider().grade(PAGES, RUBRIC, "m").result
    check_result(good, RUBRIC, 2)

    def mutated(fn):
        r = good.model_copy(deep=True)
        fn(r)
        return r

    cases = {
        "page": mutated(lambda r: r.pages.pop()),
        "not in the rubric": mutated(lambda r: r.pages[0].question_numbers.append("9")),
        "Unknown question": mutated(lambda r: setattr(r.questions[0], "question_number", "zz")),
        "more than once": mutated(lambda r: setattr(r.questions[1], "question_number", "1")),
        "exceeds max": mutated(lambda r: setattr(r.questions[0], "score", 5)),
        "below the minimum": mutated(lambda r: setattr(r.questions[0], "score", -5)),
        "Missing grades": mutated(lambda r: r.questions.pop()),
    }
    for fragment, bad in cases.items():
        with pytest.raises(GraderError, match=fragment) as exc:
            check_result(bad, RUBRIC, 2)
        assert exc.value.retryable


def test_negative_marks_are_accepted_down_to_minus_max():
    good = FakeProvider().grade(PAGES, RUBRIC, "m").result
    r = good.model_copy(deep=True)
    r.questions[0].score = -RUBRIC.questions[0].max_points
    check_result(r, RUBRIC, 2)  # exactly the floor is fine


def test_note_is_optional_and_kept_separate_from_feedback():
    from app.grader.base import QuestionResult

    q = QuestionResult(question_number="1", score=1, feedback="Score: 1/4", confidence=0.5)
    assert q.note == ""  # models may omit it
    q = QuestionResult(question_number="1", score=1, feedback="Score: 1/4", note="check page 2", confidence=0.5)
    assert q.note == "check page 2" and "check page 2" not in q.feedback


def test_registry():
    assert set(PROVIDERS) == {"claude_cli", "claude_runner", "anthropic_api", "fake"}
    assert isinstance(get_provider(Settings(grader_provider="fake")), FakeProvider)
    assert isinstance(get_provider(Settings(grader_provider="claude_cli")), ClaudeCliProvider)
    with pytest.raises(ValueError, match="Unknown GRADER_PROVIDER"):
        get_provider(Settings(grader_provider="nope"))
    with pytest.raises(GraderError, match="not implemented"):
        get_provider(Settings(grader_provider="anthropic_api")).grade(PAGES, RUBRIC, "m")


def _real_pages(n):
    """n small but genuine PNG pages (the contact sheets decode them), page k showing 'ROLL k' near the top."""
    import fitz
    from app.grader.base import PageImage

    out = []
    for k in range(1, n + 1):
        with fitz.open() as d:
            p = d.new_page(width=300, height=420)
            p.insert_text((20, 40), f"ROLL {k}", fontsize=20)
            out.append(PageImage(index=k, png=p.get_pixmap(dpi=72).tobytes("png")))
    return out


def _split_json(*splits):
    import json

    return json.dumps({"structured_output": {"splits": [
        {"student_external_id": i, "student_name": "", "first_page": a, "last_page": b, "confidence": 0.9}
        for i, a, b in splits
    ]}, "result": ""})


class RecordingExec:
    """Stands in for the CLI: records each call and answers from a script of (id, first, last) lists."""

    def __init__(self, answers):
        from app.grader.claude_cli import ExecResult

        self.answers, self.calls, self.Result = list(answers), [], ExecResult

    def run(self, model, prompt, files, timeout, json_schema=None, inline_images=False):
        self.calls.append({"model": model, "prompt": prompt, "files": sorted(files), "inline": inline_images})
        return self.Result(0, _split_json(*self.answers.pop(0)), "")

    def status(self):
        return {"ready": True}


def test_propose_split_sends_contact_sheets_in_one_inline_call():
    ex = RecordingExec([[("1001", 1, 4), ("1002", 5, 12)]])
    res = ClaudeCliProvider(executor=ex).propose_split(_real_pages(12), "haiku")
    assert [(s.student_external_id, s.first_page, s.last_page) for s in res.splits] == [("1001", 1, 4), ("1002", 5, 12)]
    assert len(ex.calls) == 1 and ex.calls[0]["inline"] is True
    assert ex.calls[0]["files"] == ["sheet-1.png", "sheet-2.png"]  # 12 pages, 6 per sheet
    assert "CONTACT SHEETS" in ex.calls[0]["prompt"] and "pages 1 to 12 of a 12-page PDF" in ex.calls[0]["prompt"]


def test_propose_split_normalizes_list_and_fenced_result():
    import json
    from app.grader.claude_cli import ClaudeExecutor, ExecResult

    class ListExec(ClaudeExecutor):
        def run(self, model, prompt, files, timeout, json_schema=None, inline_images=False):
            return ExecResult(
                0,
                json.dumps(
                    {
                        "structured_output": None,
                        "result": '```json\n[{"student_external_id":"1001","student_name":"","first_page":1,"last_page":2,"confidence":0.9}]\n```',
                    }
                ),
                "",
            )

        def status(self):
            return {"ready": True}

    res = ClaudeCliProvider(executor=ListExec()).propose_split(_real_pages(2), "haiku")
    assert len(res.splits) == 1 and res.splits[0].student_external_id == "1001"


def test_long_pdfs_are_split_into_overlapping_chunks_and_merged(monkeypatch):
    from app.grader import claude_cli

    monkeypatch.setattr(claude_cli, "SHEETS_PER_REQUEST", 3)  # 3 sheets = 18 pages per request, 1 sheet shared
    pages = _real_pages(40)  # 7 sheets: chunks of sheets 1-3, 3-5, 5-7 -> pages 1-18, 13-30, 25-40
    answers = [
        [("A", 1, 10), ("B", 11, 18)],        # chunk 1 (pages 1-18); B really runs to 20
        [("", 13, 20), ("C", 21, 30)],        # chunk 2 (13-30): sees B's tail from page 13; no roll number
        [("C", 25, 30), ("D", 31, 40)],       # chunk 3 (25-40): C seen again in the overlap
    ]
    ex = RecordingExec(answers)
    res = ClaudeCliProvider(executor=ex).propose_split(pages, "haiku")
    assert len(ex.calls) == 3 and all(c["inline"] for c in ex.calls)
    assert [(s.student_external_id, s.first_page, s.last_page) for s in res.splits] == [
        ("A", 1, 10), ("B", 11, 20), ("C", 21, 30), ("D", 31, 40),
    ]
    assert "may continue beyond page 18" in ex.calls[0]["prompt"]
    assert "page 13 looks like a continuation" in ex.calls[1]["prompt"]


def test_a_chunk_that_answers_outside_its_pages_is_retried_with_the_reason(monkeypatch):
    from app.grader import claude_cli

    monkeypatch.setattr(claude_cli, "SHEETS_PER_REQUEST", 3)
    ex = RecordingExec([[("A", 1, 18)], [("X", 2, 30)], [("B", 13, 30)], [("B", 25, 40)]])
    res = ClaudeCliProvider(executor=ex).propose_split(_real_pages(40), "haiku")
    assert "before page 13" in ex.calls[2]["prompt"]  # the rejected second answer is fed back
    assert [s.first_page for s in res.splits][0] == 1 and res.splits[-1].last_page == 40


def test_merge_chunk_splits_cuts_the_overlap_in_the_middle():
    from app.grader.base import SplitProposal, merge_chunk_splits

    def sp(i, a, b, conf=0.9):
        return SplitProposal(student_external_id=i, first_page=a, last_page=b, confidence=conf)

    # chunk 1 sees pages 1-18, chunk 2 sees 13-30; the middle of the overlap is page 15
    out = merge_chunk_splits([(1, 18, [sp("A", 1, 14), sp("B", 15, 18)]), (13, 30, [sp("B", 13, 22, 0.6), sp("C", 23, 30)])])
    assert [(s.student_external_id, s.first_page, s.last_page, s.confidence) for s in out] == [
        ("A", 1, 14, 0.9), ("B", 15, 22, 0.6), ("C", 23, 30, 0.9)
    ]
    # different people touching at the cut stay separate; an unreadable id continues the paper before it
    out = merge_chunk_splits([(1, 10, [sp("A", 1, 5), sp("B", 6, 10)]), (6, 20, [sp("Z", 6, 20)])])
    assert [(s.student_external_id, s.first_page, s.last_page) for s in out] == [("A", 1, 5), ("B", 6, 8), ("Z", 9, 20)]


def test_check_split_result_enforces_allowed_roll_numbers():
    from app.grader.base import SplitProposal, SplitResult, check_split_result

    r = SplitResult(splits=[SplitProposal(student_external_id="9999", first_page=1, last_page=2, confidence=0.9)])
    with pytest.raises(GraderError, match="not in allowed list"):
        check_split_result(r, 2, allowed_roll_numbers=["1001", "1002"])
    check_split_result(r, 2, allowed_roll_numbers=["9999"])


def test_inline_command_has_no_tools_and_the_message_carries_the_images():
    import json
    from app.grader.claude_cli import build_command, build_inline_message, extract_result_event

    cmd = build_command(["claude"], "haiku", False, {"type": "object"}, inline_images=True)
    assert cmd[cmd.index("--tools") + 1] == "" and "--allowedTools" not in cmd
    assert cmd[cmd.index("--input-format") + 1] == "stream-json" and "--verbose" in cmd and "--restricted" in cmd
    graded = build_command(["claude"], "haiku", False, {"type": "object"})  # the grading call is unchanged
    assert graded[graded.index("--tools") + 1] == "Read" and "--input-format" not in graded

    msg = json.loads(build_inline_message("PROMPT", {"sheet-10.png": b"b", "sheet-2.png": b"a"}))
    content = msg["message"]["content"]
    assert content[0] == {"type": "text", "text": "PROMPT"}
    assert [c["text"] for c in content if c["type"] == "text"][1:] == ["[sheet-2]", "[sheet-10]"]  # natural order
    assert sum(c["type"] == "image" for c in content) == 2

    out = '{"type":"system"}\n{"type":"assistant"}\n{"type":"result","is_error":false}\n'
    assert json.loads(extract_result_event(out))["type"] == "result"
    assert extract_result_event("plain text") == "plain text"
