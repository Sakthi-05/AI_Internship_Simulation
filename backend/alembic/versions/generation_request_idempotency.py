"""Add a client request key for idempotent internship generation."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "gen_req_idemp_001"
down_revision = "persist_review_failure_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("simulations", sa.Column("generation_request_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint(
        "uq_simulations_student_generation_request",
        "simulations",
        ["student_id", "generation_request_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_simulations_student_generation_request", "simulations", type_="unique")
    op.drop_column("simulations", "generation_request_id")
