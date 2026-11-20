import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    Table,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Role(str, enum.Enum):
    admin = "admin"
    ta = "ta"


class SubmissionStatus(str, enum.Enum):
    uploaded = "uploaded"
    queued = "queued"
    grading = "grading"
    ready_for_review = "ready_for_review"
    reviewed = "reviewed"
    failed = "failed"


class GradeStatus(str, enum.Enum):
    pending = "pending"
    reviewed = "reviewed"
    overridden = "overridden"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.ta)
    active: Mapped[bool] = mapped_column(default=True)
    # Bumped whenever the password changes; session tokens carry the value they were issued under,
    # so a password change signs out every other session.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Exam(Base):
    __tablename__ = "exams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    model_override: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    rubrics: Mapped[list["Rubric"]] = relationship(back_populates="exam")


class Rubric(Base):
    __tablename__ = "rubrics"
    __table_args__ = (UniqueConstraint("exam_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    exam: Mapped[Exam] = relationship(back_populates="rubrics")
    # In the order the rubric was written (see Question.position), not sorted by number.
    questions: Mapped[list["Question"]] = relationship(
        back_populates="rubric", order_by="[Question.position, Question.id]"
    )


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    rubric_id: Mapped[int] = mapped_column(ForeignKey("rubrics.id"))
    number: Mapped[str] = mapped_column(String(20))
    # 0-based place in the rubric as authored. Drives the order everywhere: rubric editor, grade page,
    # export columns and the rubric handed to the grader.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    text: Mapped[str] = mapped_column(Text, default="")
    max_points: Mapped[float] = mapped_column(Float)
    expected_answer: Mapped[str] = mapped_column(Text, default="")
    # [{"description": str, "points": float}]
    criteria: Mapped[list] = mapped_column(JSON, default=list)
    rubric: Mapped[Rubric] = relationship(back_populates="questions")


class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")


submission_tas = Table(
    "submission_tas",
    Base.metadata,
    Column("submission_id", ForeignKey("submissions.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id"), primary_key=True),
)


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("exam_id", "student_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"))
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"))
    rubric_id: Mapped[int | None] = mapped_column(ForeignKey("rubrics.id"), nullable=True)
    pdf_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus), default=SubmissionStatus.uploaded, index=True
    )
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when a worker claims the job; used to recover jobs orphaned by a crashed worker.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Which worker container claimed it; a restarted worker requeues its own orphaned jobs at once.
    claimed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    student: Mapped[Student] = relationship()
    # Several TAs may be assigned to the same paper (e.g. double-marking).
    tas: Mapped[list[User]] = relationship(secondary=submission_tas)
    pages: Mapped[list["Page"]] = relationship(
        back_populates="submission", order_by="Page.index"
    )
    grades: Mapped[list["Grade"]] = relationship(back_populates="submission")

    @property
    def ta_ids(self) -> list[int]:
        return sorted(t.id for t in self.tas)


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    image_path: Mapped[str] = mapped_column(String(500))
    transcription: Mapped[str] = mapped_column(Text, default="")
    question_numbers: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    submission: Mapped[Submission] = relationship(back_populates="pages")


class Grade(Base):
    __tablename__ = "grades"
    __table_args__ = (UniqueConstraint("submission_id", "question_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    # AI output is immutable; TA edits go in final_*.
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_feedback: Mapped[str] = mapped_column(Text, default="")
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[GradeStatus] = mapped_column(Enum(GradeStatus), default=GradeStatus.pending)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Optimistic lock: TA edits must send the version they read; stale writes get a 409.
    version: Mapped[int] = mapped_column(Integer, default=1)
    submission: Mapped[Submission] = relationship(back_populates="grades")
    question: Mapped[Question] = relationship()


class GradingRun(Base):
    __tablename__ = "grading_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    raw_output: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
