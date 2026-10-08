"""Persist explicit failures for technical reviews without inventing a score.

Revision ID: persist_review_failure_001
Revises: allow_unscored_ai_reviews_001
"""
from alembic import op
import sqlalchemy as sa


revision = "persist_review_failure_001"
down_revision = "allow_unscored_ai_reviews_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE ai_review_status_enum ADD VALUE IF NOT EXISTS 'FAILED'")
    op.alter_column("ai_reviews", "result", existing_type=sa.Enum(name="ai_review_result_enum"), nullable=True)
    op.alter_column("ai_reviews", "severity", existing_type=sa.Enum(name="ai_review_severity_enum"), nullable=True)


def downgrade() -> None:
    # PostgreSQL cannot remove a native enum value in place. Failed reviews
    # must be resolved before downgrading the nullable result fields.
    op.alter_column("ai_reviews", "severity", existing_type=sa.Enum(name="ai_review_severity_enum"), nullable=False)
    op.alter_column("ai_reviews", "result", existing_type=sa.Enum(name="ai_review_result_enum"), nullable=False)
