"""FEATURE 10: Meetings."""
import uuid
import hashlib
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.models.company import Company
from app.models.enums import MeetingType, NotificationType, SimulationStatus
from app.models.meeting import MeetingSchedule
from app.models.project import Project
from app.models.simulation import Simulation
from app.schemas.misc import MeetingComplete, MeetingCreate, MeetingRead, MeetingResponse, MeetingUpdate
from app.services.activity_log_service import log_activity
from app.services.notification_service import notify

router = APIRouter(prefix="/api/projects/{project_id}/meetings", tags=["Meetings"])


def _simulation_for(db: Session, project_id: uuid.UUID, student_id: str):
    return db.execute(select(Simulation).where(
        Simulation.project_id == project_id,
        Simulation.student_id == student_id,
    ).order_by(Simulation.created_at.desc())).scalars().first()


def _owned_project(project_id, db, user) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    company = db.get(Company, project.company_id)
    if company is None or company.student_id != user.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your project")
    return project


@router.get("", response_model=List[MeetingRead])
def list_meetings(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    _owned_project(project_id, db, user)
    stmt = (
        select(MeetingSchedule)
        .where(MeetingSchedule.project_id == project_id)
        .order_by(MeetingSchedule.scheduled_at)
    )
    return db.execute(stmt).scalars().all()


@router.post("", response_model=MeetingRead, status_code=status.HTTP_201_CREATED)
def invite_to_meeting(
    project_id: uuid.UUID,
    payload: MeetingCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = _owned_project(project_id, db, user)
    if payload.idempotency_key:
        existing = db.execute(select(MeetingSchedule).where(
            MeetingSchedule.project_id == project_id,
            MeetingSchedule.idempotency_key == payload.idempotency_key,
        )).scalar_one_or_none()
        if existing is not None:
            return existing
    simulation = _simulation_for(db, project_id, user.student_id)
    meeting = MeetingSchedule(
        project_id=project_id,
        simulation_id=simulation.id if simulation else None,
        meeting_type=payload.meeting_type,
        scheduled_at=payload.scheduled_at,
        agenda=payload.agenda,
        participants=payload.participants or ["You"],
        idempotency_key=payload.idempotency_key,
    )
    db.add(meeting)
    db.flush()
    notify(
        db, project.company_id, NotificationType.MEETING_SCHEDULED,
        f"Meeting invitation: {meeting.agenda}",
        simulation_id=simulation.id if simulation else None,
        context={"meeting_id": str(meeting.id), "project_id": str(project_id), "scheduled_at": meeting.scheduled_at.isoformat()},
        dedupe_key=f"meeting-invite:{meeting.id}",
    )
    log_activity(db, project.company_id, actor="manager", action="meeting:invited", detail=meeting.agenda, meeting_id=str(meeting.id))
    db.commit()
    db.refresh(meeting)
    return meeting


@router.patch("/{meeting_id}", response_model=MeetingRead)
def update_meeting(
    project_id: uuid.UUID,
    meeting_id: uuid.UUID,
    payload: MeetingUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = _owned_project(project_id, db, user)
    meeting = db.get(MeetingSchedule, meeting_id)
    if meeting is None or meeting.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide a meeting update")
    if meeting.completed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Completed meetings cannot be rescheduled")
    if all(getattr(meeting, key) == value for key, value in changes.items()):
        return meeting
    previous_values = {key: getattr(meeting, key).isoformat() if hasattr(getattr(meeting, key), "isoformat") else getattr(meeting, key) for key in changes}
    previous_revision = meeting.updated_at.isoformat() if meeting.updated_at else "unversioned"
    for key, value in changes.items():
        setattr(meeting, key, value)
    db.add(meeting)
    db.flush()
    summary = f"Meeting updated: {meeting.agenda} ({meeting.scheduled_at.isoformat()})"
    transition = {
        "revision": previous_revision,
        "previous": previous_values,
        "changes": changes,
    }
    fingerprint = hashlib.sha256(json.dumps(
        transition,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value),
    ).encode("utf-8")).hexdigest()[:18]
    simulation = db.get(Simulation, meeting.simulation_id) if meeting.simulation_id else _simulation_for(db, project_id, user.student_id)
    notify(
        db, project.company_id, NotificationType.MEETING_SCHEDULED, summary,
        simulation_id=simulation.id if simulation else None,
        context={"meeting_id": str(meeting.id), "project_id": str(project_id), "updated_fields": sorted(changes)},
        dedupe_key=f"meeting-update:{meeting.id}:{fingerprint}",
    )
    log_activity(
        db, project.company_id, actor=user.student_id, action="meeting:updated", detail=summary,
        meeting_id=str(meeting.id), updated_fields=sorted(changes), previous=previous_values,
        updated={key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in changes.items()},
    )
    db.commit()
    db.refresh(meeting)
    return meeting


@router.post("/{meeting_id}/respond")
def respond_to_meeting(
    project_id: uuid.UUID,
    meeting_id: uuid.UUID,
    payload: MeetingResponse,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = _owned_project(project_id, db, user)
    meeting = db.get(MeetingSchedule, meeting_id)
    if meeting is None or meeting.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    simulation = db.get(Simulation, meeting.simulation_id) if meeting.simulation_id else _simulation_for(db, project_id, user.student_id)
    if payload.idempotency_key and simulation is not None:
        from app.models.simulation_interaction import SimulationInteraction
        previous = db.execute(select(SimulationInteraction).where(
            SimulationInteraction.simulation_id == simulation.id,
            SimulationInteraction.metadata_json["idempotency_key"].as_string() == payload.idempotency_key,
        )).scalar_one_or_none()
        if previous is not None:
            return {"meeting_id": str(meeting.id), "response": previous.student_response, "recorded_at": previous.occurred_at.isoformat()}
    if meeting.student_response == payload.response:
        return {"meeting_id": str(meeting.id), "response": meeting.student_response, "recorded_at": meeting.updated_at.isoformat()}

    meeting.student_response = payload.response
    db.add(meeting)
    db.flush()
    if simulation is not None:
        from app.services.simulation.interaction_history_service import record_interaction
        record_interaction(
            db, simulation,
            situation=f"Meeting invitation: {meeting.agenda}",
            student_response=payload.response,
            decision="respond_to_meeting",
            interaction_type="meeting_response",
            task_id=simulation.current_task_id,
            metadata={"meeting_id": str(meeting.id), "meeting_type": meeting.meeting_type.value, "idempotency_key": payload.idempotency_key},
        )
    log_activity(db, project.company_id, actor=user.student_id, action="meeting:responded", detail=payload.response[:500], meeting_id=str(meeting.id))
    db.commit()
    db.refresh(meeting)
    return {"meeting_id": str(meeting.id), "response": meeting.student_response, "recorded_at": meeting.updated_at.isoformat()}


@router.patch("/{meeting_id}/complete", response_model=MeetingRead)
def complete_meeting(
    project_id: uuid.UUID,
    meeting_id: uuid.UUID,
    payload: MeetingComplete,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    project = _owned_project(project_id, db, user)
    meeting = db.get(MeetingSchedule, meeting_id)
    if meeting is None or meeting.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if meeting.completed:
        same = (
            meeting.notes == payload.notes
            and list(meeting.attendance or []) == list(payload.attendance or [])
            and list(meeting.action_items or []) == list(payload.action_items or [])
        )
        if same:
            return meeting
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Meeting completion is already recorded")
    meeting.completed = True
    meeting.notes = payload.notes
    meeting.attendance = payload.attendance
    meeting.action_items = payload.action_items
    db.add(meeting)
    log_activity(
        db, project.company_id, actor=user.student_id, action="meeting:completed",
        detail=meeting.agenda,
    )
    simulation = db.get(Simulation, meeting.simulation_id) if meeting.simulation_id else _simulation_for(db, project_id, user.student_id)
    if simulation is not None:
        from app.services.simulation.interaction_history_service import record_interaction
        record_interaction(
            db, simulation,
            situation=f"Completed meeting: {meeting.agenda}",
            student_response=payload.notes,
            decision="complete_meeting",
            interaction_type="meeting_completed",
            task_id=simulation.current_task_id,
            metadata={"meeting_id": str(meeting.id), "attendance": payload.attendance, "action_items": payload.action_items},
        )
    db.commit()
    db.refresh(meeting)
    return meeting
