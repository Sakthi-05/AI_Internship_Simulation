"""Add metadata JSONB columns for dynamic baseline assessment, dynamic tasks, sprints, and simulation state.

Revision ID: add_dynamic_simulation_metadata_001
Revises: persist_review_failure_001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "dyn_metadata_001"
down_revision = "gen_req_idemp_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessment_attempts",
        sa.Column("generation_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb"))
    )
    op.add_column(
        "tasks",
        sa.Column("task_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb"))
    )
    op.add_column(
        "sprints",
        sa.Column("sprint_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb"))
    )
    op.add_column(
        "simulations",
        sa.Column("simulation_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'{}'::jsonb"))
    )


def downgrade() -> None:
    op.drop_column("simulations", "simulation_metadata")
    op.drop_column("sprints", "sprint_metadata")
    op.drop_column("tasks", "task_metadata")
    op.drop_column("assessment_attempts", "generation_metadata")
