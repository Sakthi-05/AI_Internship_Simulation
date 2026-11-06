"""Add private immutable baseline assessment snapshots.

Revision ID: baseline_attempt_snapshots_001
Revises: student_technical_domain_ratings_001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "baseline_attempt_snapshots_001"
down_revision = "student_technical_domain_ratings_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessment_attempts",
        sa.Column("assessment_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_attempts", "assessment_snapshot")
