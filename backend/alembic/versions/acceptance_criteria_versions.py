"""Store acceptance-criteria versions and allow a later requirement change.

Existing tasks, events, and the acceptance_criteria array are left in place.
Snapshots are added beside them. The one-row requirement-change index is
replaced with an index that allows another change after the previous one is
resolved. The new index is skipped when a task already has two open changes,
and those rows are not deleted.

Revision ID: acceptance_criteria_versions_001
Revises: requirement_change_unique_001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "acceptance_criteria_versions_001"
down_revision = "requirement_change_unique_001"
branch_labels = None
depends_on = None

_OPEN_PREDICATE = sa.text(
    "event_type = 'REQUIREMENT_CHANGE' "
    "AND status IN ('pending', 'acknowledged') "
    "AND task_id IS NOT NULL"
)


def upgrade() -> None:
    op.create_table(
        "acceptance_criteria_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workflow_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_event_id"], ["workflow_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "version_number", name="uq_acceptance_criteria_versions_task_number"),
    )
    op.create_index("ix_acceptance_criteria_versions_task_id", "acceptance_criteria_versions", ["task_id"])
    op.create_index(
        "ix_acceptance_criteria_versions_status",
        "acceptance_criteria_versions",
        ["task_id", "status"],
    )
    op.create_index(
        "ix_acceptance_criteria_versions_event_id",
        "acceptance_criteria_versions",
        ["workflow_event_id"],
    )

    bind = op.get_bind()
    bind.execute(sa.text("DROP INDEX IF EXISTS uq_workflow_events_one_requirement_change"))
    open_duplicate = bind.execute(sa.text(
        """
        SELECT 1
        FROM workflow_events
        WHERE event_type = 'REQUIREMENT_CHANGE'
          AND status IN ('pending', 'acknowledged')
          AND task_id IS NOT NULL
        GROUP BY simulation_id, task_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )).first()
    if open_duplicate is None:
        op.create_index(
            "uq_workflow_events_one_open_requirement_change",
            "workflow_events",
            ["simulation_id", "task_id"],
            unique=True,
            postgresql_where=_OPEN_PREDICATE,
        )

    from sqlalchemy.orm import Session
    from app.services.criteria_version_service import backfill_missing_versions

    session = Session(bind=bind)
    backfill_missing_versions(session)
    session.flush()


def downgrade() -> None:
    op.drop_index("uq_workflow_events_one_open_requirement_change", table_name="workflow_events")
    op.drop_index("ix_acceptance_criteria_versions_event_id", table_name="acceptance_criteria_versions")
    op.drop_index("ix_acceptance_criteria_versions_status", table_name="acceptance_criteria_versions")
    op.drop_index("ix_acceptance_criteria_versions_task_id", table_name="acceptance_criteria_versions")
    op.drop_table("acceptance_criteria_versions")
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
    if duplicate is None:
        op.create_index(
            "uq_workflow_events_one_requirement_change",
            "workflow_events",
            ["simulation_id", "task_id"],
            unique=True,
            postgresql_where=sa.text("event_type = 'REQUIREMENT_CHANGE' AND task_id IS NOT NULL"),
        )
