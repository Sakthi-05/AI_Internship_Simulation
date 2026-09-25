"""Add Module 1 accounts, skills, and assessment tables."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "add_module1_student_001"
down_revision = "add_adaptive_intelligence_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student_profiles", sa.Column("college", sa.String(200), nullable=True))
    op.add_column("student_profiles", sa.Column("degree", sa.String(150), nullable=True))
    op.add_column("student_profiles", sa.Column("year_of_study", sa.Integer(), nullable=True))
    op.add_column("student_profiles", sa.Column("career_goal", sa.String(200), nullable=True))
    op.add_column(
        "student_profiles",
        sa.Column("recommended_parameters", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(30), nullable=False, server_default="student"),
        sa.Column("student_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_student_id", "users", ["student_id"], unique=True)

    op.create_table(
        "skills",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(80), nullable=False, server_default="technical"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_skills_name", "skills", ["name"], unique=True)

    op.create_table(
        "student_skills",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("student_id", sa.String(100), nullable=False),
        sa.Column("skill_id", UUID(as_uuid=True), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("self_rating", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("student_id", "skill_id", name="uq_student_skills_student_skill"),
    )
    op.create_index("ix_student_skills_student_id", "student_skills", ["student_id"])

    op.create_table(
        "assessment_questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("competency", sa.String(80), nullable=False),
        sa.Column("question_type", sa.String(30), nullable=False, server_default="mcq"),
        sa.Column("options", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("correct_option", sa.String(20), nullable=True),
        sa.Column("rubric_keywords", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("difficulty", sa.String(30), nullable=False, server_default="intermediate"),
        sa.Column("domain", sa.String(80), nullable=False, server_default="software"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_assessment_questions_competency", "assessment_questions", ["competency"])

    op.create_table(
        "assessment_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("student_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="in_progress"),
        sa.Column("question_ids", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("competencies", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_assessment_attempts_student_id", "assessment_attempts", ["student_id"])

    op.create_table(
        "assessment_answers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("attempt_id", UUID(as_uuid=True), sa.ForeignKey("assessment_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", UUID(as_uuid=True), sa.ForeignKey("assessment_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("response", sa.Text(), nullable=False, server_default=""),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("competency", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_assessment_answers_attempt_id", "assessment_answers", ["attempt_id"])


def downgrade() -> None:
    op.drop_table("assessment_answers")
    op.drop_table("assessment_attempts")
    op.drop_table("assessment_questions")
    op.drop_table("student_skills")
    op.drop_table("skills")
    op.drop_table("users")
    op.drop_column("student_profiles", "recommended_parameters")
    op.drop_column("student_profiles", "career_goal")
    op.drop_column("student_profiles", "year_of_study")
    op.drop_column("student_profiles", "degree")
    op.drop_column("student_profiles", "college")
