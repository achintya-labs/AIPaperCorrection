"""users.ldap_uid: link a cached user row to its LDAP entry (SSO mode)

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("ldap_uid", sa.String(length=255), nullable=True))
        batch.create_unique_constraint("uq_users_ldap_uid", ["ldap_uid"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_ldap_uid", type_="unique")
        batch.drop_column("ldap_uid")
