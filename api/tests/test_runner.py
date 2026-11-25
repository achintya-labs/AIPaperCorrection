import json
import stat
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from app.claude_runner import create_app
from app.config import Settings
from app.grader import get_provider
from app.grader.base import GraderError, PageImage, QuestionSpec, RubricSpec
from app.grader.claude_cli import ClaudeCliProvider, LocalExecutor, RemoteExecutor
from app.grader.fake import FakeProvider
from tests.test_auth import client, login  # noqa: F401  (fixture reuse)
from tests.test_grader import FAKE_CLAUDE, PAGES, RUBRIC, _real_pages

TOKEN = "s3cret-token"


def write_script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture()
def runner(tmp_path, monkeypatch):
    """A runner app wrapping the stand-in `claude`, plus a RemoteExecutor wired to it in-process."""
    script = write_script(tmp_path / "claude", FAKE_CLAUDE)
    log = tmp_path / "calls.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_MODE", "ok")
    app = create_app(executor=LocalExecutor(script), token=TOKEN)
    http = TestClient(app)  # an httpx.Client, so RemoteExecutor can use it directly

    class R:
        pass

    r = R()
    r.http, r.log, r.app = http, log, app
    r.auth = {"Authorization": f"Bearer {TOKEN}"}
    r.remote = lambda token=TOKEN: RemoteExecutor("http://runner", token, client=http)
    r.calls = lambda: json.loads(log.read_text())
    r.mode = lambda m: monkeypatch.setenv("FAKE_MODE", m)
    return r


def test_refuses_to_start_without_token():
    with pytest.raises(RuntimeError, match="CLAUDE_RUNNER_TOKEN"):
        create_app(executor=LocalExecutor("claude"), token="")


def test_auth_required(runner):
    assert runner.http.get("/health").status_code == 200
    for path, method in (("/status", "get"), ("/run", "post")):
        assert getattr(runner.http, method)(path).status_code == 401
        bad = getattr(runner.http, method)(path, headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401
    assert runner.http.get("/openapi.json").status_code == 404  # no schema/docs exposed


def test_full_grading_through_runner(runner):
    provider = ClaudeCliProvider(executor=runner.remote(), name="claude_runner")
    out = provider.grade(PAGES, RUBRIC, "claude-test-model")
    assert [q.score for q in out.result.questions] == [2, 3]
    (call,) = runner.calls()
    args = call["args"]
    assert args[args.index("--model") + 1] == "claude-test-model"
    assert args[args.index("--tools") + 1] == "Read" and "--restricted" in args
    assert call["files"] == ["page-1.png", "page-2.png", "rubric.json"]
    assert "page-1.png ... page-2.png" in call["prompt"]


def test_split_through_the_runner_sends_the_images_inline_with_no_tools(runner):
    runner.mode("split")
    provider = ClaudeCliProvider(executor=runner.remote(), name="claude_runner")
    res = provider.propose_split(_real_pages(2), "haiku")
    assert [(s.student_external_id, s.first_page, s.last_page) for s in res.splits] == [("1001", 1, 2)]
    (call,) = runner.calls()
    args = call["args"]
    assert args[args.index("--input-format") + 1] == "stream-json" and args[args.index("--output-format") + 1] == "stream-json"
    assert args[args.index("--tools") + 1] == "" and "--allowedTools" not in args and "--restricted" in args
    assert call["images"] == 1  # 2 pages fit on one contact sheet
    assert call["files"] == []  # nothing was written for the CLI to open


def test_caller_cannot_choose_cli_flags(runner):
    r = runner.http.post(
        "/run",
        headers=runner.auth,
        data={"model": "m", "prompt": "p", "args": "--dangerously-skip-permissions", "tools": "Bash"},
        files=[("files", ("rubric.json", json.dumps({"questions": []}))), ("files", ("page-1.png", b"x"))],
    )
    assert r.status_code == 200
    args = runner.calls()[0]["args"]
    assert "--dangerously-skip-permissions" not in args and "Bash" not in args
    assert args[args.index("--tools") + 1] == "Read"


@pytest.mark.parametrize(
    "files,model",
    [
        ([("files", ("../evil.png", b"x"))], "m"),
        ([("files", ("evil.sh", b"x"))], "m"),
        ([("files", ("page-1.png", b"x")), ("files", ("page-1.png", b"y"))], "m"),
        ([("files", ("page-1.png", b"x"))], "bad model; rm -rf /"),
    ],
)
def test_input_validation(runner, files, model):
    r = runner.http.post("/run", headers=runner.auth, data={"model": model, "prompt": "p"}, files=files)
    assert r.status_code == 422
    assert not runner.log.exists()  # the CLI was never started


def test_request_size_limit(tmp_path, monkeypatch):
    script = write_script(tmp_path / "claude", FAKE_CLAUDE)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "l.json"))
    http = TestClient(create_app(executor=LocalExecutor(script), token=TOKEN, max_request_mb=1))
    r = http.post(
        "/run",
        headers={"Authorization": f"Bearer {TOKEN}"},
        data={"model": "m", "prompt": "p"},
        files=[("files", ("page-1.png", b"x" * (2 * 1024 * 1024)))],
    )
    assert r.status_code == 413


def test_timeout_maps_to_504_and_grader_error(runner):
    runner.mode("sleep")
    r = runner.http.post(
        "/run", headers=runner.auth, data={"model": "m", "prompt": "p", "timeout": "1"},
        files=[("files", ("page-1.png", b"x")), ("files", ("rubric.json", b"{}"))],
    )
    assert r.status_code == 504
    with pytest.raises(GraderError, match="timed out"):
        ClaudeCliProvider(executor=runner.remote(), timeout_seconds=1).grade(PAGES, RUBRIC, "m")


def test_cli_failure_is_reported_not_retried(runner):
    runner.mode("crash")
    with pytest.raises(GraderError, match="exited 3: boom"):
        ClaudeCliProvider(executor=runner.remote(), max_attempts=3).grade(PAGES, RUBRIC, "m")
    assert len(runner.calls()) == 1


def test_wrong_token_and_unreachable_runner(runner):
    with pytest.raises(GraderError, match="rejected CLAUDE_RUNNER_TOKEN"):
        ClaudeCliProvider(executor=runner.remote("wrong")).grade(PAGES, RUBRIC, "m")
    dead = RemoteExecutor("http://127.0.0.1:9", TOKEN, client=httpx.Client())
    with pytest.raises(GraderError, match="unreachable"):
        ClaudeCliProvider(executor=dead).grade(PAGES, RUBRIC, "m")


def test_remote_executor_needs_config():
    with pytest.raises(GraderError, match="must be set"):
        RemoteExecutor("", "")
    with pytest.raises(GraderError, match="must be set"):
        get_provider(Settings(grader_provider="claude_runner", claude_runner_url="http://x"))


def test_registry_builds_runner_provider():
    p = get_provider(Settings(grader_provider="claude_runner", claude_runner_url="http://c:9000", claude_runner_token="t"))
    assert p.name == "claude_runner" and isinstance(p.executor, RemoteExecutor)


# --- login status ---


@pytest.fixture()
def auth_script(tmp_path):
    """A stand-in for `claude auth status --json`."""
    def make(logged_in):
        return write_script(
            tmp_path / "claude-auth",
            f"#!{sys.executable}\nimport json\nprint(json.dumps({{'loggedIn': {logged_in}, 'authMethod': 'claude.ai'}}))\n",
        )
    return make


def test_local_status(auth_script, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert LocalExecutor(auth_script(True)).status()["ready"] is True
    out = LocalExecutor(auth_script(False)).status()
    assert out["ready"] is False and out["detail"] == "Not logged in"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # an API key counts as authenticated
    assert LocalExecutor(auth_script(False)).status()["ready"] is True
    assert LocalExecutor("/nonexistent/claude").status()["ready"] is False


def test_status_through_runner(tmp_path, auth_script, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    http = TestClient(create_app(executor=LocalExecutor(auth_script(False)), token=TOKEN))
    provider = ClaudeCliProvider(executor=RemoteExecutor("http://r", TOKEN, client=http), name="claude_runner")
    assert provider.status() == {"provider": "claude_runner", "ready": False, "auth_method": "claude.ai", "detail": "Not logged in"}
    bad = ClaudeCliProvider(executor=RemoteExecutor("http://r", "wrong", client=http))
    assert bad.status()["ready"] is False and "rejected" in bad.status()["detail"]


def test_grader_status_endpoint(client, monkeypatch):
    monkeypatch.setattr("app.routes.grading.get_provider", lambda: FakeProvider())
    login(client, "admin@x.io")
    assert client.get("/grader/status").json() == {"provider": "fake", "ready": True}

    def boom():
        raise GraderError("CLAUDE_RUNNER_URL and CLAUDE_RUNNER_TOKEN must be set")
    monkeypatch.setattr("app.routes.grading.get_provider", boom)
    out = client.get("/grader/status").json()
    assert out["ready"] is False and "must be set" in out["detail"]

    client.post("/auth/logout")
    login(client, "ta@x.io")
    assert client.get("/grader/status").status_code == 403


def test_the_runner_imports_without_pymupdf():
    """The `claude` container installs only the runner's few dependencies, not PyMuPDF (the worker builds the
    contact sheets). A top-level `import fitz` anywhere the runner imports crashed that container at start-up."""
    import subprocess
    from pathlib import Path

    api_dir = Path(__file__).resolve().parent.parent
    code = "import sys; sys.modules['fitz'] = None; import app.claude_runner, app.grader.claude_cli, app.grader.base"
    r = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(api_dir)!r}); {code}"],
                       capture_output=True, text=True, env={"CLAUDE_RUNNER_TOKEN": "t"})
    assert r.returncode == 0, r.stderr
