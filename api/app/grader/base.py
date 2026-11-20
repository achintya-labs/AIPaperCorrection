from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, Field, ValidationError


class QuestionSpec(BaseModel):
    number: str
    text: str = ""
    max_points: float
    expected_answer: str = ""
    criteria: list[dict] = []


class RubricSpec(BaseModel):
    questions: list[QuestionSpec]


class PageImage(BaseModel):
    index: int  # 1-based
    png: bytes


class PageResult(BaseModel):
    page: int = Field(ge=1, description="1-based page number")
    question_numbers: list[str] = Field(
        description="Rubric question numbers that have any answer content on this page"
    )
    transcription: str = Field(description="Faithful transcription; use [illegible] for unreadable text")
    confidence: float = Field(
        ge=0, le=1, description="How reliably this page was read and its questions identified"
    )


class QuestionResult(BaseModel):
    question_number: str
    score: float = Field(ge=0)
    feedback: str
    confidence: float = Field(
        ge=0, le=1, description="Likelihood that a careful human grader would agree with this score"
    )


class GradingResult(BaseModel):
    """What the model must return. Also used (via its JSON schema) to constrain provider output."""

    pages: list[PageResult]
    questions: list[QuestionResult]
    overall_confidence: float = Field(ge=0, le=1)


class GradingOutcome(BaseModel):
    result: GradingResult
    raw_output: str = ""
    model: str = ""
    usage: dict = {}


class GraderError(Exception):
    """`retryable` errors are fed back to the model on the next attempt (e.g. invalid output)."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def check_result(result: GradingResult, rubric: RubricSpec, n_pages: int) -> None:
    """Provider-independent sanity checks on a grading result. Raises retryable GraderError."""

    def fail(msg: str):
        raise GraderError(msg, retryable=True)

    page_nums = sorted(p.page for p in result.pages)
    if page_nums != list(range(1, n_pages + 1)):
        fail(f"Expected exactly one entry for each page 1..{n_pages}, got pages {page_nums}")

    by_number = {q.number: q for q in rubric.questions}
    for p in result.pages:
        unknown = [n for n in p.question_numbers if n not in by_number]
        if unknown:
            fail(f"Page {p.page} lists question numbers not in the rubric: {unknown}")

    seen: set[str] = set()
    for q in result.questions:
        spec = by_number.get(q.question_number)
        if spec is None:
            fail(f"Unknown question number {q.question_number!r}")
        if q.question_number in seen:
            fail(f"Question {q.question_number} graded more than once")
        seen.add(q.question_number)
        if q.score > spec.max_points:
            fail(f"Question {q.question_number}: score {q.score} exceeds max {spec.max_points}")
    missing = sorted(set(by_number) - seen)
    if missing:
        fail(f"Missing grades for questions {missing}")


class GraderProvider(ABC):
    """
    Pluggable grading backend. Implement `_attempt` only; `grade` adds validation and retries,
    so every provider gets the same guarantees about what reaches the database.
    """

    name: ClassVar[str]

    def __init__(self, max_attempts: int = 2):
        self.max_attempts = max(1, max_attempts)

    def status(self) -> dict:
        """Readiness for the admin UI (e.g. "is Claude logged in?"). Providers override when useful."""
        return {"provider": self.name, "ready": True}

    @abstractmethod
    def _attempt(
        self,
        pages: list[PageImage],
        rubric: RubricSpec,
        model: str,
        feedback: str | None,
    ) -> GradingOutcome:
        """One call to the backend. `feedback` describes why the previous attempt was rejected."""

    def grade(self, pages: list[PageImage], rubric: RubricSpec, model: str) -> GradingOutcome:
        feedback: str | None = None
        last: GraderError | None = None
        for _ in range(self.max_attempts):
            try:
                outcome = self._attempt(pages, rubric, model, feedback)
                check_result(outcome.result, rubric, len(pages))
                return outcome
            except GraderError as e:
                if not e.retryable:
                    raise
                last, feedback = e, str(e)
        assert last is not None
        raise GraderError(f"Gave up after {self.max_attempts} attempts: {last}")


def validation_message(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
