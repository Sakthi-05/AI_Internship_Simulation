"""Allow Alembic revision identifiers longer than 32 characters.

Revision ID: alembic_version_width_001
Revises: acceptance_criteria_versions_001
"""
from alembic import op
import sqlalchemy as sa


revision = "alembic_version_width_001"
down_revision = "acceptance_criteria_versions_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=128),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
