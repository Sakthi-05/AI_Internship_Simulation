"""Add cooldown lookup index on notifications table.

Supports efficient duplicate-suppression queries in notification_service.notify().
The index covers (company_id, notification_type, created_at) so that the
cooldown window query can filter to recent rows without a full table scan.

Revision ID: notification_cooldown_001
Revises: student_technical_domain_ratings_001
"""
from alembic import op
import sqlalchemy as sa


revision = "notification_cooldown_001"
down_revision = "baseline_attempt_snapshots_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_notifications_cooldown_lookup",
        "notifications",
        ["company_id", "notification_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_cooldown_lookup", table_name="notifications")
