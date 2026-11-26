"""Quota probes: one tiny Haiku request that reports how much of the plan or API rate limit is used.

Subscription (Claude Pro/Max login): the CLI prints `rate_limit_event` messages in stream-json output. Their
`rate_limit_info` carries the 5-hour and 7-day windows (`unifiedWindows.five_hour|seven_day`, each with a
`utilization` fraction and `resetsAt` epoch seconds), or a single window (`rateLimitType`, `utilization`, `resetsAt`).
API key: the Messages API returns `anthropic-ratelimit-*` response headers on every call.

Both are normalised to one shape so the UI draws bars without caring where the numbers came from:
  {"source": "subscription" | "api_key", "model": str, "checked_at": ISO-8601, "windows": [
      {"key", "label", "used_percent": float | None, "resets_at": ISO-8601 | None, "status": str | None, "detail": str | None}]}
"""

import json
import os
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.grader.base import GraderError

PROMPT = "Reply with the single word: ok"
CLI_MODEL = "haiku"
API_MODEL = "claude-haiku-5-5"
API_URL = "https://api.anthropic.com/v1/messages"

_LABELS = {
    "five_hour": "Session (5-hour)",
    "seven_day": "Weekly (7-day)",
    "seven_day_opus": "Weekly · Opus",
    "seven_day_sonnet": "Weekly · Sonnet",
    "seven_day_overage_included": "Weekly (incl. extra usage)",
    "overage": "Extra usage",
}
_ORDER = list(_LABELS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(epoch) -> str | None:
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _percent(utilization) -> float | None:
    """`utilization` is a fraction of the limit (1 = at the limit). Returns 0-100, or None when not a number."""
    if isinstance(utilization, bool) or not isinstance(utilization, (int, float)):
        return None
    return round(max(0.0, float(utilization)) * 100, 1)


def windows_from_events(stdout: str) -> list[dict]:
    """Newest information per window from every rate_limit_event in the CLI's stream-json output."""
    found: dict[str, dict] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "rate_limit_event":
            continue
        info = event.get("rate_limit_info")
        if not isinstance(info, dict):
            continue
        status = info.get("status") if isinstance(info.get("status"), str) else None
        unified = info.get("unifiedWindows")
        if isinstance(unified, dict):
            for key, w in unified.items():
                if isinstance(w, dict):
                    found[key] = {
                        "used_percent": _percent(w.get("utilization")),
                        "resets_at": _iso(w.get("resetsAt")),
                        "status": w.get("status") or status,
                    }
        key = info.get("rateLimitType")
        if isinstance(key, str):  # the single window this event is about (fills in what unifiedWindows lacks)
            cur = found.setdefault(key, {"used_percent": None, "resets_at": None, "status": status})
            cur["used_percent"] = _percent(info.get("utilization")) if cur["used_percent"] is None else cur["used_percent"]
            cur["resets_at"] = _iso(info.get("resetsAt")) or cur["resets_at"]
            cur["status"] = cur["status"] or status
    keys = sorted(found, key=lambda k: _ORDER.index(k) if k in _ORDER else len(_ORDER))
    return [{"key": k, "label": _LABELS.get(k, k.replace("_", " ").title()), "detail": None, **found[k]} for k in keys]


def probe_subscription(claude_cmd: list[str], bare: bool = False, model: str = CLI_MODEL, timeout: int = 90) -> dict:
    """Run one minimal, tool-less Haiku turn through the CLI and read the rate limits it reports."""
    cmd = [
        *claude_cmd, "-p", "--model", model,
        "--output-format", "stream-json", "--verbose",
        "--tools", "",
        "--restricted", "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
    ]
    if bare:
        cmd.append("--bare")
    with tempfile.TemporaryDirectory(prefix="usage-") as tmp:
        try:
            proc = subprocess.run(cmd, input=PROMPT, cwd=Path(tmp), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise GraderError(f"The quota check timed out after {timeout}s")
        except OSError as e:
            raise GraderError(f"Could not run the Claude CLI: {e}")
    windows = windows_from_events(proc.stdout)
    if not windows:
        hint = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise GraderError(
            "Claude did not report any rate limit. Is it signed in with a Pro/Max plan (API-key logins have no plan "
            f"limits)? {hint}".strip()
        )
    return {"source": "subscription", "model": model, "checked_at": _now(), "windows": windows}


def _header_window(headers, prefix: str, key: str, label: str) -> dict | None:
    limit, remaining = headers.get(f"{prefix}-limit"), headers.get(f"{prefix}-remaining")
    try:
        limit_n, remaining_n = int(limit), int(remaining)
    except (TypeError, ValueError):
        return None
    used = max(0, limit_n - remaining_n)
    return {
        "key": key,
        "label": label,
        "used_percent": round(100 * used / limit_n, 1) if limit_n > 0 else None,
        "resets_at": headers.get(f"{prefix}-reset"),
        "status": None,
        "detail": f"{remaining_n:,} of {limit_n:,} left",
    }


def probe_api_key(api_key: str, model: str = API_MODEL, timeout: int = 30, client: httpx.Client | None = None) -> dict:
    """One 1-token Messages API call; the response headers carry the organisation's current rate limits."""
    try:
        r = (client or httpx).post(
            API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise GraderError(f"Could not reach the Anthropic API: {e}")
    if r.status_code in (401, 403):
        raise GraderError("The Anthropic API rejected the API key")
    if r.status_code not in (200, 429):  # 429 still carries the headers: that is exactly when you want to see them
        raise GraderError(f"Anthropic API error {r.status_code}: {r.text[:200]}")
    h = r.headers
    windows = [
        w for w in (
            _header_window(h, "anthropic-ratelimit-requests", "requests", "Requests per minute"),
            _header_window(h, "anthropic-ratelimit-tokens", "tokens", "Tokens per minute"),
            _header_window(h, "anthropic-ratelimit-input-tokens", "input_tokens", "Input tokens per minute"),
            _header_window(h, "anthropic-ratelimit-output-tokens", "output_tokens", "Output tokens per minute"),
        ) if w
    ]
    if not windows:
        raise GraderError("The Anthropic API did not return rate limit headers")
    return {"source": "api_key", "model": model, "checked_at": _now(), "windows": windows}


def probe(claude_bin: str = "claude", bare: bool = False) -> dict:
    """Whichever applies here: an ANTHROPIC_API_KEY in the environment means API limits, else the plan limits."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return probe_api_key(key)
    return probe_subscription(shlex.split(claude_bin), bare)


def sample() -> dict:
    """Canned numbers for the fake grader, so the UI can be tried without Claude."""
    return {
        "source": "subscription", "model": "fake", "checked_at": _now(),
        "windows": [
            {"key": "five_hour", "label": _LABELS["five_hour"], "used_percent": 34.0, "resets_at": None, "status": "allowed", "detail": None},
            {"key": "seven_day", "label": _LABELS["seven_day"], "used_percent": 62.0, "resets_at": None, "status": "allowed", "detail": None},
        ],
    }
