"""Add simulation context and idempotency to existing communication records.

Revision ID: communication_context_001
Revises: baseline_attempt_snapshots_001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "communication_context_001"
down_revision = "baseline_attempt_snapshots_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("messages", sa.Column("idempotency_key", sa.String(length=120), nullable=True))
    op.add_column("messages", sa.Column("in_reply_to_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_messages_simulation_id_simulations", "messages", "simulations", ["simulation_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_messages_in_reply_to_id_messages", "messages", "messages", ["in_reply_to_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_messages_simulation_id", "messages", ["simulation_id"])
    op.create_unique_constraint("uq_messages_sender_idempotency", "messages", ["company_id", "sender_id", "idempotency_key"])

    op.add_column("meeting_schedules", sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("meeting_schedules", sa.Column("student_response", sa.Text(), nullable=True))
    op.add_column("meeting_schedules", sa.Column("idempotency_key", sa.String(length=120), nullable=True))
    op.create_foreign_key("fk_meeting_schedules_simulation_id_simulations", "meeting_schedules", "simulations", ["simulation_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_meeting_schedules_simulation_id", "meeting_schedules", ["simulation_id"])
    op.create_unique_constraint("uq_meetings_project_idempotency", "meeting_schedules", ["project_id", "idempotency_key"])

    op.add_column("notifications", sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("notifications", sa.Column("dedupe_key", sa.String(length=250), nullable=True))
    op.add_column("notifications", sa.Column("context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_foreign_key("fk_notifications_simulation_id_simulations", "notifications", "simulations", ["simulation_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_notifications_simulation_id", "notifications", ["simulation_id"])
    op.create_unique_constraint("uq_notifications_company_dedupe", "notifications", ["company_id", "dedupe_key"])

    # Attach old records to the most recently created simulation for their owner.
    op.execute("""
        UPDATE messages m SET simulation_id = latest.id
        FROM (SELECT DISTINCT ON (company_id) company_id, id FROM simulations ORDER BY company_id, created_at DESC) latest
        WHERE m.company_id = latest.company_id AND m.simulation_id IS NULL
    """)
    op.execute("""
        UPDATE notifications n SET simulation_id = latest.id
        FROM (SELECT DISTINCT ON (company_id) company_id, id FROM simulations ORDER BY company_id, created_at DESC) latest
        WHERE n.company_id = latest.company_id AND n.simulation_id IS NULL
    """)
    op.execute("""
        UPDATE meeting_schedules m SET simulation_id = latest.id
        FROM (SELECT DISTINCT ON (project_id) project_id, id FROM simulations ORDER BY project_id, created_at DESC) latest
        WHERE m.project_id = latest.project_id AND m.simulation_id IS NULL
    """)


def downgrade() -> None:
    op.drop_constraint("uq_notifications_company_dedupe", "notifications", type_="unique")
    op.drop_index("ix_notifications_simulation_id", table_name="notifications")
    op.drop_constraint("fk_notifications_simulation_id_simulations", "notifications", type_="foreignkey")
    op.drop_column("notifications", "context_json")
    op.drop_column("notifications", "dedupe_key")
    op.drop_column("notifications", "simulation_id")

    op.drop_index("ix_meeting_schedules_simulation_id", table_name="meeting_schedules")
    op.drop_constraint("uq_meetings_project_idempotency", "meeting_schedules", type_="unique")
    op.drop_constraint("fk_meeting_schedules_simulation_id_simulations", "meeting_schedules", type_="foreignkey")
    op.drop_column("meeting_schedules", "student_response")
    op.drop_column("meeting_schedules", "idempotency_key")
    op.drop_column("meeting_schedules", "simulation_id")

    op.drop_constraint("uq_messages_sender_idempotency", "messages", type_="unique")
    op.drop_index("ix_messages_simulation_id", table_name="messages")
    op.drop_constraint("fk_messages_in_reply_to_id_messages", "messages", type_="foreignkey")
    op.drop_constraint("fk_messages_simulation_id_simulations", "messages", type_="foreignkey")
    op.drop_column("messages", "in_reply_to_id")
    op.drop_column("messages", "idempotency_key")
    op.drop_column("messages", "simulation_id")
