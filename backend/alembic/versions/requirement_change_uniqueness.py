"""One requirement-change event per simulation task.

Existing rows are left in place. The unique index is added only when no
task already has more than one requirement change, so this upgrade does
not delete or rewrite stored events.

Revision ID: requirement_change_unique_001
Revises: soft_skill_evidence_001
"""
from alembic import op
import sqlalchemy as sa


revision = "requirement_change_unique_001"
down_revision = "soft_skill_evidence_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(sa.text(
        """
        SELECT 1
        FROM workflow_events
        WHERE event_type = 'REQUIREMENT_CHANGE'
          AND task_id IS NOT NULL
        GROUP BY simulation_id, task_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )).first()
    if duplicate is not None:
        return
    op.create_index(
        "uq_workflow_events_one_requirement_change",
        "workflow_events",
        ["simulation_id", "task_id"],
        unique=True,
        postgresql_where=sa.text("event_type = 'REQUIREMENT_CHANGE' AND task_id IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_workflow_events_one_requirement_change'"
    )).first()
    if exists is None:
        return
    op.drop_index("uq_workflow_events_one_requirement_change", table_name="workflow_events")
