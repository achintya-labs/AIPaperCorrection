from collections.abc import Callable

from app.config import Settings, get_settings
from app.grader.anthropic_api import AnthropicApiProvider
from app.grader.base import GraderError, GraderProvider
from app.grader.claude_cli import ClaudeCliProvider, RemoteExecutor
from app.grader.fake import FakeProvider

__all__ = ["GraderError", "GraderProvider", "get_provider", "PROVIDERS"]

# Register new backends here; selected by the GRADER_PROVIDER setting.
PROVIDERS: dict[str, Callable[[Settings], GraderProvider]] = {
    "claude_cli": lambda s: ClaudeCliProvider(
        claude_bin=s.claude_bin,
        timeout_seconds=s.grader_timeout_seconds,
        bare=s.claude_bare,
        max_attempts=s.grader_max_attempts,
        split_top_fraction=s.split_top_fraction,
    ),
    # Same provider, but the CLI runs in the dedicated `claude` container.
    "claude_runner": lambda s: ClaudeCliProvider(
        executor=RemoteExecutor(s.claude_runner_url, s.claude_runner_token),
        timeout_seconds=s.grader_timeout_seconds,
        max_attempts=s.grader_max_attempts,
        name="claude_runner",
        split_top_fraction=s.split_top_fraction,
    ),
    "anthropic_api": lambda s: AnthropicApiProvider(max_attempts=s.grader_max_attempts),
    "fake": lambda s: FakeProvider(max_attempts=s.grader_max_attempts),
}


def get_provider(settings: Settings | None = None) -> GraderProvider:
    s = settings or get_settings()
    try:
        return PROVIDERS[s.grader_provider](s)
    except KeyError:
        raise ValueError(f"Unknown GRADER_PROVIDER {s.grader_provider!r}; options: {sorted(PROVIDERS)}")
