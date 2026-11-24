from app.grader.base import (
    GraderProvider,
    GradingOutcome,
    GradingResult,
    PageImage,
    PageResult,
    QuestionResult,
    RubricSpec,
    SplitProposal,
    SplitResult,
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

    def propose_split(
        self, pages: list[PageImage], model: str, allowed_roll_numbers: list[str] | None = None
    ) -> SplitResult:
        if not pages:
            return SplitResult(splits=[])
        step = 2 if len(pages) > 1 else 1
        splits: list[SplitProposal] = []
        for idx, start in enumerate(range(1, len(pages) + 1, step), start=1):
            end = min(start + step - 1, len(pages))
            sid = (
                allowed_roll_numbers[idx - 1]
                if allowed_roll_numbers and idx - 1 < len(allowed_roll_numbers)
                else ("" if allowed_roll_numbers else f"s{idx}")
            )
            splits.append(
                SplitProposal(
                    student_external_id=sid,
                    student_name="",
                    first_page=start,
                    last_page=end,
                    confidence=0.8,
                )
            )
        return SplitResult(splits=splits)
