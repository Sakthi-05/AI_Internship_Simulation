"""Add SPRINT_COMPLETED to the event type enum.

Revision ID: add_sprint_completed_event_001
Revises: add_dynamic_simulation_metadata_001
"""
from alembic import op


revision = "sprint_completed_enum_001"
down_revision = "dyn_metadata_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_type_enum ADD VALUE IF NOT EXISTS 'SPRINT_COMPLETED'")
    op.execute("ALTER TYPE event_type_enum ADD VALUE IF NOT EXISTS 'sprint_completed'")


def downgrade() -> None:
    pass