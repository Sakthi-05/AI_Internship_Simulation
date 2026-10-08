"""Allow an AI review to explicitly represent missing score evidence.

Revision ID: allow_unscored_ai_reviews_001
Revises: add_module2_event_labels_001
"""
from alembic import op
import sqlalchemy as sa


revision = "allow_unscored_ai_reviews_001"
down_revision = "add_module2_event_labels_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("ai_reviews", "score", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("ai_reviews", "score", existing_type=sa.Integer(), nullable=False)
