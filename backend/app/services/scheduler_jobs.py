"""
Background jobs (APScheduler), satisfying the "APScheduler (background
events)" requirement with one real, useful job: periodically checking
every project's live ProjectState for high stress and pushing a manager
check-in notification when it crosses a threshold — driven by real state,
not a timer-only random event.
"""
import logging

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.enums import NotificationType
from app.models.activity_log import ActivityLog
from app.models.project import Project
from app.models.project_state import ProjectState
from app.services.activity_log_service import log_activity
from app.services.notification_service import notify
from app.websockets.manager import manager as ws_manager

logger = logging.getLogger("aiwse.scheduler")

HIGH_STRESS_THRESHOLD = 80


async def send_proactive_workplace_messages() -> None:
    """Check for real overdue or blocked tasks; never send on elapsed time alone."""
    try:
        from app.services import proactive_chat_service as proactive
        await proactive.sweep_proactive_messages()
    except Exception:
        logger.exception("send_proactive_workplace_messages job failed")


async def check_high_stress_projects() -> None:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(ProjectState, Project)
            .join(Project, Project.id == ProjectState.project_id)
        ).all()

        for state, project in rows:
            latest_event = db.execute(
                select(ActivityLog.action).where(
                    ActivityLog.company_id == project.company_id,
                    ActivityLog.metadata_json["project_id"].as_string() == str(project.id),
                    ActivityLog.action.in_(("scheduler:stress_checkin", "scheduler:stress_recovered")),
                ).order_by(ActivityLog.created_at.desc()).limit(1)
            ).scalar_one_or_none()
            if state.stress_level < HIGH_STRESS_THRESHOLD:
                if latest_event == "scheduler:stress_checkin":
                    log_activity(
                        db, project.company_id, actor="system", action="scheduler:stress_recovered",
                        detail=f"Stress returned below {HIGH_STRESS_THRESHOLD} on '{project.title}'.",
                        project_id=str(project.id),
                    )
                    db.commit()
                continue
            if latest_event == "scheduler:stress_checkin":
                continue
            message = (
                f"Stress levels on '{project.title}' are high ({state.stress_level}/100). "
                "The manager is checking in — consider addressing blockers or missed deadlines."
            )
            notify(db, project.company_id, NotificationType.DEADLINE_UPDATED, message)
            log_activity(
                db, project.company_id, actor="system", action="scheduler:stress_checkin",
                detail=message, project_id=str(project.id), stress_level=state.stress_level,
            )
            db.commit()

            await ws_manager.broadcast(
                str(project.company_id), "notification", {"message": message}
            )
    except Exception:
        logger.exception("check_high_stress_projects job failed")
        db.rollback()
    finally:
        db.close()
