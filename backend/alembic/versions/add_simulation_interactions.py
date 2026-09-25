"""add simulation_interactions table for unified interaction history

Revision ID: add_sim_interaction_001
Revises: add_task_path_001, 3033103205d5
Create Date: 2026-09-08

Additive only: creates simulation_interactions. Does not drop or truncate
existing tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "add_sim_interaction_001"
down_revision = ("add_task_path_001", "3033103205d5")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulation_interactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "simulation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("simulations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("student_id", sa.String(length=100), nullable=False),
        sa.Column(
            "scenario_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scenarios.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("situation", sa.Text(), nullable=False),
        sa.Column("student_response", sa.Text(), nullable=True),
        sa.Column("decision", sa.String(length=100), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("interaction_type", sa.String(length=80), nullable=False),
        sa.Column(
            "parent_interaction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("simulation_interactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_simulation_interactions_simulation_id",
        "simulation_interactions",
        ["simulation_id"],
    )
    op.create_index(
        "ix_simulation_interactions_student_id",
        "simulation_interactions",
        ["student_id"],
    )
    op.create_index(
        "ix_simulation_interactions_scenario_id",
        "simulation_interactions",
        ["scenario_id"],
    )
    op.create_index(
        "ix_simulation_interactions_task_id",
        "simulation_interactions",
        ["task_id"],
    )
    op.create_index(
        "ix_simulation_interactions_occurred_at",
        "simulation_interactions",
        ["occurred_at"],
    )
    op.create_index(
        "ix_simulation_interactions_parent_id",
        "simulation_interactions",
        ["parent_interaction_id"],
    )
    op.create_index(
        "ix_simulation_interactions_simulation_sequence",
        "simulation_interactions",
        ["simulation_id", "sequence"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_simulation_interactions_simulation_sequence",
        table_name="simulation_interactions",
    )
    op.drop_index("ix_simulation_interactions_parent_id", table_name="simulation_interactions")
    op.drop_index("ix_simulation_interactions_occurred_at", table_name="simulation_interactions")
    op.drop_index("ix_simulation_interactions_task_id", table_name="simulation_interactions")
    op.drop_index("ix_simulation_interactions_scenario_id", table_name="simulation_interactions")
    op.drop_index("ix_simulation_interactions_student_id", table_name="simulation_interactions")
    op.drop_index("ix_simulation_interactions_simulation_id", table_name="simulation_interactions")
    op.drop_table("simulation_interactions")
