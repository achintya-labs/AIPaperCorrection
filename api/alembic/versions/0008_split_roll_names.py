"""split job roster names (roll number -> name)

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("split_jobs", sa.Column("roll_names", sa.JSON(), server_default="{}", nullable=False))


def downgrade() -> None:
    op.drop_column("split_jobs", "roll_names")
