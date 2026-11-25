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


def _norm_name(name: str) -> str:
    return " ".join(name.casefold().split())


def apply_roster(result: SplitResult, names: dict[str, str]) -> None:
    """Make the roster's names authoritative. A split whose roll number is on the roster gets the roster's name (Claude
    may have misread the handwriting). A split with no roll number but a readable name that matches exactly one roster
    name gets that name's roll number."""
    if not names:
        return
    by_name: dict[str, list[str]] = {}
    for roll, name in names.items():
        if _norm_name(name):
            by_name.setdefault(_norm_name(name), []).append(roll)
    for sp in result.splits:
        if sp.student_external_id in names:
            if names[sp.student_external_id]:
                sp.student_name = names[sp.student_external_id]
        elif not sp.student_external_id and sp.student_name:
            rolls = by_name.get(_norm_name(sp.student_name), [])
            if len(rolls) == 1:
                sp.student_external_id, sp.student_name = rolls[0], names[rolls[0]]


def merge_chunk_splits(chunks: list[tuple[int, int, list[SplitProposal]]]) -> list[SplitProposal]:
    """Join the results of consecutive chunks of one long PDF into a single list of papers.

    Each chunk is (first page, last page, its splits); consecutive chunks overlap. Every page is decided by exactly
    one chunk: the overlap is cut in the middle, the earlier chunk deciding up to the cut and the later one after it,
    because a chunk sees the pages near its own edges with the least context. Splits are clipped to their chunk's
    range, and two pieces that now touch at a cut are one paper when the later one has no roll number or the same one.
    """
    cuts = [(chunks[i + 1][0] + chunks[i][1]) // 2 for i in range(len(chunks) - 1)]
    pieces: list[tuple[int, SplitProposal]] = []
    for i, (lo, hi, splits) in enumerate(chunks):
        a = lo if i == 0 else cuts[i - 1] + 1
        b = hi if i == len(chunks) - 1 else cuts[i]
        for sp in splits:
            first, last = max(sp.first_page, a), min(sp.last_page, b)
            if first <= last:
                pieces.append((i, sp.model_copy(update={"first_page": first, "last_page": last})))
    pieces.sort(key=lambda t: (t[1].first_page, t[1].last_page))
    out: list[tuple[int, SplitProposal]] = []
    for i, sp in pieces:
        if out:
            pi, prev = out[-1]
            touching = sp.first_page == prev.last_page + 1
            if i != pi and touching and (not sp.student_external_id or sp.student_external_id == prev.student_external_id):
                prev.last_page = sp.last_page
                prev.student_name = prev.student_name or sp.student_name
                prev.confidence = min(prev.confidence, sp.confidence)
                continue
        out.append((i, sp))
    return [sp for _, sp in out]


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
        self,
        pages: list[PageImage],
        model: str,
        allowed_roll_numbers: list[str] | None = None,
        roll_names: dict[str, str] | None = None,
    ) -> SplitResult:
        """Propose how a combined PDF's pages split into individual student papers.

        `roll_names` (roll number -> name, optional) is the roster's names, a second clue for matching a paper."""
        raise GraderError(f"Auto-split is not supported by provider {self.name!r}")


def validation_message(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
