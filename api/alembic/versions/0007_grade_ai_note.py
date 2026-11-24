"""grade reviewer note (ai_note)

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("grades", sa.Column("ai_note", sa.Text(), server_default="", nullable=False))


def downgrade() -> None:
    op.drop_column("grades", "ai_note")
