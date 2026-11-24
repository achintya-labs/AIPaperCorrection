"""split jobs

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "split_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exam_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("pdf_path", sa.String(length=500), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "processing", "ready", "failed", name="splitjobstatus"),
            nullable=False,
        ),
        sa.Column("splits", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["exam_id"], ["exams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_split_jobs_exam_id"), "split_jobs", ["exam_id"], unique=False)
    op.create_index(op.f("ix_split_jobs_status"), "split_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_split_jobs_token"), "split_jobs", ["token"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_split_jobs_token"), table_name="split_jobs")
    op.drop_index(op.f("ix_split_jobs_status"), table_name="split_jobs")
    op.drop_index(op.f("ix_split_jobs_exam_id"), table_name="split_jobs")
    op.drop_table("split_jobs")
    sa.Enum("queued", "processing", "ready", "failed", name="splitjobstatus").drop(op.get_bind(), checkfirst=True)
