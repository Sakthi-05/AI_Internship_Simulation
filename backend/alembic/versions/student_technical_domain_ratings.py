"""Add current technical-domain selection and domain-scoped self-ratings.

Revision ID: student_technical_domain_ratings_001
Revises: acceptance_criteria_versions_001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "student_technical_domain_ratings_001"
down_revision = "alembic_version_width_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "student_profiles",
        sa.Column("technical_domain_id", sa.String(length=80), nullable=True),
    )
    op.create_table(
        "student_technical_technology_ratings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", sa.String(length=100), nullable=False),
        sa.Column("technical_domain_id", sa.String(length=80), nullable=False),
        sa.Column("technology_id", sa.String(length=120), nullable=False),
        sa.Column("self_rating", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["student_id"], ["student_profiles.student_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "student_id",
            "technical_domain_id",
            "technology_id",
            name="uq_student_technical_rating_domain_technology",
        ),
        sa.CheckConstraint(
            "self_rating >= 1 AND self_rating <= 5",
            name="ck_student_technical_rating_self_rating_range",
        ),
    )
    op.create_index(
        "ix_student_technical_ratings_student_id",
        "student_technical_technology_ratings",
        ["student_id"],
    )
    op.create_index(
        "ix_student_technical_ratings_domain_id",
        "student_technical_technology_ratings",
        ["technical_domain_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_student_technical_ratings_domain_id",
        table_name="student_technical_technology_ratings",
    )
    op.drop_index(
        "ix_student_technical_ratings_student_id",
        table_name="student_technical_technology_ratings",
    )
    op.drop_table("student_technical_technology_ratings")
    op.drop_column("student_profiles", "technical_domain_id")
