"""Add enum labels the ORM writes for task start and submission events.

The original event and notification enums store member names. A later
migration added the same events as lowercase values, so inserts of
TASK_STARTED failed against the live database.
"""
from alembic import op

revision = "add_module2_event_labels_001"
down_revision = "add_module2_company_001"
branch_labels = None
depends_on = None

_EVENT_LABELS = (
    "TASK_STARTED",
    "SUBMISSION_CREATED",
    "SUBMISSION_VERSION_CREATED",
    "REVIEW_STARTED",
    "AI_REVIEW_COMPLETED",
    "CHANGES_REQUESTED",
    "SUBMISSION_RESUBMITTED",
    "MANAGER_APPROVED",
    "TASK_COMPLETED",
)

_NOTIFICATION_LABELS = (
    "TASK_STARTED",
    "TASK_SUBMITTED",
    "TASK_APPROVED",
    "TASK_REJECTED",
    "CHANGES_REQUESTED",
    "PROJECT_COMPLETED",
    "SUBMISSION_RECEIVED",
    "REVIEW_STARTED",
    "REVIEW_COMPLETED",
    "SUBMISSION_APPROVED",
    "TASK_COMPLETED",
    "DEPENDENCY_BLOCKED",
)


def upgrade() -> None:
    for label in _EVENT_LABELS:
        op.execute(f"ALTER TYPE event_type_enum ADD VALUE IF NOT EXISTS '{label}'")
    for label in _NOTIFICATION_LABELS:
        op.execute(f"ALTER TYPE notification_type_enum ADD VALUE IF NOT EXISTS '{label}'")


def downgrade() -> None:
    # PostgreSQL cannot drop a single enum label without rebuilding the type.
    pass
