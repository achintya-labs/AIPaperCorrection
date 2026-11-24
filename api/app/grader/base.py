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


def score_floor(max_points: float) -> float:
    """Lowest mark a question can carry. Negative marking is allowed, down to minus the question's maximum."""
    return -max_points


class QuestionResult(BaseModel):
    question_number: str
    score: float  # may be negative (negative marking); range is checked against the rubric in check_result
    feedback: str
    note: str = Field(default="", description="Remarks for the reviewing TA only; not shown to the student")
    confidence: float = Field(
        ge=0, le=1, description="Likelihood that a careful human grader would agree with this score"
    )


class GradingResult(BaseModel):
    """What the model must return. Also used (via its JSON schema) to constrain provider output."""

    pages: list[PageResult]
    questions: list[QuestionResult]
    overall_confidence: float = Field(ge=0, le=1)


class SplitProposal(BaseModel):
    student_external_id: str = Field(description="Student roll number or ID extracted from the pages; empty if unreadable")
    student_name: str = Field(default="", description="Student name if visible, else empty string")
    first_page: int = Field(ge=1, description="1-based inclusive start page")
    last_page: int = Field(ge=1, description="1-based inclusive end page")
    confidence: float = Field(ge=0, le=1, description="Confidence in the split boundaries and roll number")


class SplitResult(BaseModel):
    splits: list[SplitProposal] = Field(min_length=1)


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
        if q.score < score_floor(spec.max_points):
            fail(f"Question {q.question_number}: score {q.score} is below the minimum {score_floor(spec.max_points):g}")
    missing = sorted(set(by_number) - seen)
    if missing:
        fail(f"Missing grades for questions {missing}")


def check_split_result(
    result: SplitResult, n_pages: int, allowed_roll_numbers: list[str] | None = None
) -> None:
    """Validate proposed PDF split boundaries. Raises retryable GraderError."""

    def fail(msg: str):
        raise GraderError(msg, retryable=True)

    if not result.splits:
        fail("Expected at least one paper split")

    result.splits.sort(key=lambda s: (s.first_page, s.last_page))
    last_end = 0
    for sp in result.splits:
        if sp.first_page < 1 or sp.last_page < sp.first_page or sp.last_page > n_pages:
            fail(f"Split range {sp.first_page}-{sp.last_page} is invalid for {n_pages} page(s)")
        if sp.first_page <= last_end:
            fail(f"Split range {sp.first_page}-{sp.last_page} overlaps another split")
        last_end = sp.last_page

    if allowed_roll_numbers:
        allowed = set(allowed_roll_numbers)
        unknown = [sp.student_external_id for sp in result.splits if sp.student_external_id and sp.student_external_id not in allowed]
        if unknown:
            fail(f"Roll number(s) not in allowed list: {unknown}")


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

    def propose_split(
        self, pages: list[PageImage], model: str, allowed_roll_numbers: list[str] | None = None
    ) -> SplitResult:
        """Propose how a combined PDF's pages split into individual student papers."""
        raise GraderError(f"Auto-split is not supported by provider {self.name!r}")


def validation_message(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
