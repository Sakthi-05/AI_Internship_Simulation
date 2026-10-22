"""Store workplace soft-skill evidence linked to a simulation event.

Revision ID: soft_skill_evidence_001
Revises: sprint_completed_enum_001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "soft_skill_evidence_001"
down_revision = "sprint_completed_enum_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "soft_skill_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("student_id", sa.String(length=100), nullable=False),
        sa.Column("simulation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workflow_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_key", sa.String(length=80), nullable=False),
        sa.Column("competency", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="insufficient_evidence"),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_id", "competency", name="uq_soft_skill_evidence_event_competency"),
    )
    op.create_index("ix_soft_skill_evidence_student_id", "soft_skill_evidence", ["student_id"])
    op.create_index("ix_soft_skill_evidence_simulation_id", "soft_skill_evidence", ["simulation_id"])
    op.create_index("ix_soft_skill_evidence_task_id", "soft_skill_evidence", ["task_id"])
    op.create_index("ix_soft_skill_evidence_event_id", "soft_skill_evidence", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_soft_skill_evidence_event_id", table_name="soft_skill_evidence")
    op.drop_index("ix_soft_skill_evidence_task_id", table_name="soft_skill_evidence")
    op.drop_index("ix_soft_skill_evidence_simulation_id", table_name="soft_skill_evidence")
    op.drop_index("ix_soft_skill_evidence_student_id", table_name="soft_skill_evidence")
    op.drop_table("soft_skill_evidence")
