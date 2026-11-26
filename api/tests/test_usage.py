import json
import stat
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from app.claude_runner import create_app
from app.grader import usage
from app.grader.base import GraderError
from app.grader.claude_cli import LocalExecutor, RemoteExecutor
from app.grader.fake import FakeProvider
from tests.test_auth import client, login  # noqa: F401  (fixture reuse)

EVENT = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed",
        "rateLimitType": "five_hour",
        "utilization": 0.4,
        "resetsAt": 1900000000,
        "unifiedWindows": {
            "five_hour": {"utilization": 0.235, "resetsAt": 1900000000},
            "seven_day": {"utilization": 0.5, "resetsAt": 1900500000, "status": "allowed_warning"},
        },
    },
}


def test_windows_from_events():
    out = "\n".join(["not json", json.dumps({"type": "system"}), json.dumps(EVENT), json.dumps({"type": "result"})])
    w = {x["key"]: x for x in usage.windows_from_events(out)}
    assert list(w) == ["five_hour", "seven_day"]  # session first, then weekly
    assert w["five_hour"]["used_percent"] == 23.5 and w["five_hour"]["label"] == "Session (5-hour)"
    assert w["five_hour"]["resets_at"].startswith("2030-03-17")
    assert w["seven_day"]["used_percent"] == 50.0 and w["seven_day"]["status"] == "allowed_warning"
    assert usage.windows_from_events("") == []


def test_single_window_event_without_unified_windows():
    ev = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "seven_day", "utilization": 0.1}}
    (w,) = usage.windows_from_events(json.dumps(ev))
    assert w["key"] == "seven_day" and w["used_percent"] == 10.0 and w["resets_at"] is None


def fake_cli(tmp_path, body):
    p = tmp_path / "claude"
    p.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_probe_subscription_runs_a_tiny_haiku_turn(tmp_path):
    claude = fake_cli(tmp_path, f"print({json.dumps(json.dumps(EVENT))})")
    out = usage.probe_subscription([claude])
    assert out["source"] == "subscription" and out["model"] == "haiku" and len(out["windows"]) == 2


def test_probe_subscription_explains_when_there_is_no_limit_info(tmp_path):
    claude = fake_cli(tmp_path, 'print(\'{"type":"result"}\')')
    with pytest.raises(GraderError, match="did not report any rate limit"):
        usage.probe_subscription([claude])


def test_probe_api_key_reads_the_ratelimit_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"], seen["body"] = request.headers["x-api-key"], json.loads(request.content)
        return httpx.Response(200, json={"content": []}, headers={
            "anthropic-ratelimit-requests-limit": "50", "anthropic-ratelimit-requests-remaining": "45",
            "anthropic-ratelimit-requests-reset": "2030-01-01T00:00:30Z",
            "anthropic-ratelimit-tokens-limit": "40000", "anthropic-ratelimit-tokens-remaining": "10000",
        })

    out = usage.probe_api_key("sk-test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen["key"] == "sk-test" and seen["body"]["max_tokens"] == 1 and "haiku" in seen["body"]["model"]
    w = {x["key"]: x for x in out["windows"]}
    assert out["source"] == "api_key" and w["requests"]["used_percent"] == 10.0
    assert w["tokens"]["used_percent"] == 75.0 and w["tokens"]["detail"] == "10,000 of 40,000 left"


def test_probe_api_key_errors():
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(GraderError, match="rejected the API key"):
        usage.probe_api_key("x", client=bad)
    nohdr = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(GraderError, match="did not return rate limit headers"):
        usage.probe_api_key("x", client=nohdr)


def test_probe_prefers_the_api_key_when_one_is_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    monkeypatch.setattr(usage, "probe_api_key", lambda key: {"source": "api_key", "key": key})
    assert usage.probe()["key"] == "sk-env"


def test_runner_usage_endpoint_and_remote_executor(tmp_path):
    claude = fake_cli(tmp_path, f"print({json.dumps(json.dumps(EVENT))})")
    http = TestClient(create_app(executor=LocalExecutor(claude), token="t"))
    assert http.post("/usage").status_code in (401, 403)
    out = RemoteExecutor("http://runner", "t", client=http).usage()
    assert [w["key"] for w in out["windows"]] == ["five_hour", "seven_day"]
    broken = TestClient(create_app(executor=LocalExecutor(fake_cli(tmp_path, "pass")), token="t"))
    r = broken.post("/usage", headers={"Authorization": "Bearer t"})
    assert r.status_code == 502 and "did not report" in r.json()["detail"]
    with pytest.raises(GraderError, match="Claude runner error 502"):
        RemoteExecutor("http://runner", "t", client=broken).usage()


def test_grader_usage_endpoint(client, monkeypatch):
    monkeypatch.setattr("app.routes.grading.get_provider", lambda: FakeProvider())
    login(client, "admin@x.io")
    r = client.post("/grader/usage")
    assert r.status_code == 200 and [w["key"] for w in r.json()["windows"]] == ["five_hour", "seven_day"]

    def boom(self):
        raise GraderError("not signed in")
    monkeypatch.setattr(FakeProvider, "usage", boom)
    r = client.post("/grader/usage")
    assert r.status_code == 502 and "not signed in" in r.json()["detail"]

    client.post("/auth/logout")
    login(client, "ta@x.io")
    assert client.post("/grader/usage").status_code == 403
