"""Read-only, administrator-only monitoring APIs."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, require_role
from app.services.admin_service import dashboard, list_students, student_detail, student_activity, analytics, recent_activity

router = APIRouter(prefix="/api/admin", tags=["Admin monitoring"])
AdminUser = Depends(require_role("admin", "administrator"))


@router.get("/dashboard")
def dashboard_route(db: Session = Depends(get_db), _admin: CurrentUser = AdminUser):
    return dashboard(db)


@router.get("/students")
def students_route(
    q: Optional[str] = None,
    baseline_status: Optional[str] = None,
    internship_status: Optional[str] = Query(default=None, pattern="^(active|completed|not_started)$"),
    simulation_status: Optional[str] = Query(default=None, pattern="^(not_started|in_progress|completed|abandoned)$"),
    difficulty: Optional[str] = None,
    activity_status: Optional[str] = None,
    readiness_min: Optional[float] = Query(default=None, ge=0, le=100),
    readiness_max: Optional[float] = Query(default=None, ge=0, le=100),
    sort: str = Query(default="registration_date", pattern="^(name|readiness|baseline|last_activity|registration_date)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: CurrentUser = AdminUser,
):
    return list_students(
        db, q=q, baseline_status=baseline_status, internship_status=internship_status,
        simulation_status=simulation_status, difficulty=difficulty,
        activity_status=activity_status, readiness_min=readiness_min,
        readiness_max=readiness_max, sort=sort, order=order, page=page, page_size=page_size,
    )


@router.get("/students/{student_id}")
def student_detail_route(student_id: str, db: Session = Depends(get_db), _admin: CurrentUser = AdminUser):
    return student_detail(db, student_id)


@router.get("/students/{student_id}/activity")
def student_activity_route(student_id: str, limit: int = Query(default=100, ge=1, le=250), db: Session = Depends(get_db), _admin: CurrentUser = AdminUser):
    return {"events": student_activity(db, student_id, limit)}


@router.get("/activity")
def activity_route(limit: int = Query(default=30, ge=1, le=100), db: Session = Depends(get_db), _admin: CurrentUser = AdminUser):
    return {"events": recent_activity(db, limit)}


@router.get("/analytics")
def analytics_route(db: Session = Depends(get_db), _admin: CurrentUser = AdminUser):
    return analytics(db)
