from app.grader.base import GraderError, GraderProvider, GradingOutcome, PageImage, RubricSpec


class AnthropicApiProvider(GraderProvider):
    """Placeholder for grading through the Messages API directly.

    Implement `_attempt` (send the page images + rubric, return a GradingOutcome); validation,
    retries and persistence are inherited, so nothing else in the system changes.
    """

    name = "anthropic_api"

    def _attempt(
        self, pages: list[PageImage], rubric: RubricSpec, model: str, feedback: str | None
    ) -> GradingOutcome:
        raise GraderError("anthropic_api provider is not implemented yet")
