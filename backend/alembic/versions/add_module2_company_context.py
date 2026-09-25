"""Store company size and technology stack on the generated company."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "add_module2_company_001"
down_revision = "add_module1_student_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("size", sa.String(40), nullable=False, server_default="small"))
    op.add_column(
        "companies",
        sa.Column("technology_stack", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("companies", "technology_stack")
    op.drop_column("companies", "size")
