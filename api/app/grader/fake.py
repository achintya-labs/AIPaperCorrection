from app.grader.base import (
    GraderProvider,
    GradingOutcome,
    GradingResult,
    PageImage,
    PageResult,
    QuestionResult,
    RubricSpec,
)


class FakeProvider(GraderProvider):
    """Deterministic provider for development and tests: half marks, spread questions over pages."""

    name = "fake"

    def _attempt(
        self, pages: list[PageImage], rubric: RubricSpec, model: str, feedback: str | None
    ) -> GradingOutcome:
        per_page: dict[int, list[str]] = {p.index: [] for p in pages}
        for i, q in enumerate(rubric.questions):
            per_page[pages[i % len(pages)].index].append(q.number)
        result = GradingResult(
            pages=[
                PageResult(
                    page=p.index,
                    question_numbers=per_page[p.index],
                    transcription=f"(fake transcription of page {p.index})",
                    confidence=0.5,
                )
                for p in pages
            ],
            questions=[
                QuestionResult(
                    question_number=q.number,
                    score=q.max_points / 2,
                    feedback="Fake grader: half marks.",
                    confidence=0.5,
                )
                for q in rubric.questions
            ],
            overall_confidence=0.5,
        )
        return GradingOutcome(result=result, raw_output="fake", model=model or "fake")
