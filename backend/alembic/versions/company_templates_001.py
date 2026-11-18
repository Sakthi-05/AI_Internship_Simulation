"""Alembic migration: create company_templates table.

Revision ID: company_templates_001
Revises:     notification_cooldown_001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "company_templates_001"
down_revision = "notification_cooldown_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "company_type",
            postgresql.ENUM(
                name="company_type_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("industry", sa.String(150), nullable=False),
        sa.Column("department", sa.String(150), nullable=False),
        sa.Column("mission", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("size", sa.String(40), nullable=False, server_default="small"),
        sa.Column(
            "technology_stack",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_company_templates_company_type", "company_templates", ["company_type"]
    )
    op.create_index(
        "ix_company_templates_is_active", "company_templates", ["is_active"]
    )
    op.create_index(
        "ix_company_templates_created_by", "company_templates", ["created_by"]
    )


def downgrade() -> None:
    op.drop_index("ix_company_templates_created_by", table_name="company_templates")
    op.drop_index("ix_company_templates_is_active", table_name="company_templates")
    op.drop_index("ix_company_templates_company_type", table_name="company_templates")
    op.drop_table("company_templates")
