"""Add adaptive AI, feedback, readiness and certificate tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "add_adaptive_intelligence_001"
down_revision = "add_sim_interaction_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("student_id", sa.String(100), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200)),
        sa.Column("domain", sa.String(150)),
        sa.Column("role", sa.String(150)),
        sa.Column("skills", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("baseline_competencies", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("current_competencies", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("strengths", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("weaknesses", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("assessment_summary", sa.Text()),
        sa.Column("recommended_difficulty", sa.String(30), nullable=False, server_default="intermediate"),
        sa.Column("last_assessed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_student_profiles_student_id", "student_profiles", ["student_id"], unique=True)

    op.create_table(
        "interaction_evaluations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("interaction_id", UUID(as_uuid=True), sa.ForeignKey("simulation_interactions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("simulation_id", UUID(as_uuid=True), sa.ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.String(100), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("competencies", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("strengths", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("weaknesses", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("mistakes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
        sa.Column("recommendations", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("branch_selected", sa.String(100)),
        sa.Column("difficulty_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.75"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_interaction_evaluations_simulation_id", "interaction_evaluations", ["simulation_id"])
    op.create_index("ix_interaction_evaluations_student_id", "interaction_evaluations", ["student_id"])

    op.create_table(
        "feedback_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("student_id", sa.String(100), nullable=False),
        sa.Column("simulation_id", UUID(as_uuid=True), sa.ForeignKey("simulations.id", ondelete="SET NULL")),
        sa.Column("overall_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("strengths", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("weaknesses", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("recommendations", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("generated_by", sa.String(50), nullable=False, server_default="rule_engine"),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_feedback_reports_student_id", "feedback_reports", ["student_id"])

    op.create_table(
        "readiness_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("student_id", sa.String(100), nullable=False),
        sa.Column("simulation_id", UUID(as_uuid=True), sa.ForeignKey("simulations.id", ondelete="SET NULL")),
        sa.Column("readiness_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("percentile", sa.Float(), nullable=False, server_default="0"),
        sa.Column("benchmark_score", sa.Float(), nullable=False, server_default="70"),
        sa.Column("gap_to_benchmark", sa.Float(), nullable=False, server_default="0"),
        sa.Column("readiness_level", sa.String(50), nullable=False, server_default="NOT_YET_PLACEMENT_READY"),
        sa.Column("forecast_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("trend_direction", sa.String(30), nullable=False, server_default="stable"),
        sa.Column("metrics", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_readiness_snapshots_student_id", "readiness_snapshots", ["student_id"])

    op.create_table(
        "early_warning_alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("student_id", sa.String(100), nullable=False),
        sa.Column("simulation_id", UUID(as_uuid=True), sa.ForeignKey("simulations.id", ondelete="SET NULL")),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(30), nullable=False, server_default="warning"),
        sa.Column("previous_score", sa.Float()),
        sa.Column("current_score", sa.Float()),
        sa.Column("trend_value", sa.Float()),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text()),
        sa.Column("status", sa.String(30), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_early_warning_alerts_student_id", "early_warning_alerts", ["student_id"])

    op.create_table(
        "certificates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("student_id", sa.String(100), nullable=False),
        sa.Column("simulation_id", UUID(as_uuid=True), sa.ForeignKey("simulations.id", ondelete="SET NULL")),
        sa.Column("certificate_number", sa.String(100), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(150), nullable=False),
        sa.Column("readiness_percentile", sa.Float(), nullable=False, server_default="0"),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("metadata_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_certificates_student_id", "certificates", ["student_id"])


def downgrade() -> None:
    op.drop_index("ix_certificates_student_id", table_name="certificates")
    op.drop_table("certificates")
    op.drop_index("ix_early_warning_alerts_student_id", table_name="early_warning_alerts")
    op.drop_table("early_warning_alerts")
    op.drop_index("ix_readiness_snapshots_student_id", table_name="readiness_snapshots")
    op.drop_table("readiness_snapshots")
    op.drop_index("ix_feedback_reports_student_id", table_name="feedback_reports")
    op.drop_table("feedback_reports")
    op.drop_index("ix_interaction_evaluations_student_id", table_name="interaction_evaluations")
    op.drop_index("ix_interaction_evaluations_simulation_id", table_name="interaction_evaluations")
    op.drop_table("interaction_evaluations")
    op.drop_index("ix_student_profiles_student_id", table_name="student_profiles")
    op.drop_table("student_profiles")
