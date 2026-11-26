from app.grader.base import GraderError, GraderProvider, GradingOutcome, PageImage, RubricSpec


class AnthropicApiProvider(GraderProvider):
    """Placeholder for grading through the Messages API directly.

    Implement `_attempt` (send the page images + rubric, return a GradingOutcome); validation,
    retries and persistence are inherited, so nothing else in the system changes.
    """

    name = "anthropic_api"

    def status(self) -> dict:
        return {"provider": self.name, "ready": False, "plan_quota": False, "detail": "Not implemented yet"}

    def usage(self) -> dict:
        import os
        from app.grader.usage import probe_api_key
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise GraderError("ANTHROPIC_API_KEY is not set")
        return probe_api_key(key)

    def _attempt(
        self, pages: list[PageImage], rubric: RubricSpec, model: str, feedback: str | None
    ) -> GradingOutcome:
        raise GraderError("anthropic_api provider is not implemented yet")
