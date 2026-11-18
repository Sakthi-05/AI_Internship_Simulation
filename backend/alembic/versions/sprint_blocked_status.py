"""Add explicit blocked state for a sprint.

Revision ID: sprint_blocked_status_001
Revises: company_templates_001
"""
from alembic import op


revision = "sprint_blocked_status_001"
down_revision = "company_templates_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE sprint_status_enum ADD VALUE IF NOT EXISTS 'BLOCKED'")


def downgrade() -> None:
    # PostgreSQL enum labels cannot be safely removed in place.
    pass
