"""Module 1 question bank and baseline assessment."""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user, require_role
from app.services.assessment_service import (
    create_question,
    delete_question,
    latest_result,
    list_questions_admin,
    start_attempt,
    submit_attempt,
    update_question,
)

router = APIRouter(tags=["Module 1 — Assessment"])


class AnswerItem(BaseModel):
    question_id: str
    response: str = Field(..., min_length=1, max_length=5000)


class SubmitAttempt(BaseModel):
    answers: List[AnswerItem] = Field(..., min_length=1)


class QuestionWrite(BaseModel):
    prompt: str = Field(..., min_length=8)
    competency: str
    question_type: str = "mcq"
    options: list = Field(default_factory=list)
    correct_option: Optional[str] = None
    rubric_keywords: List[str] = Field(default_factory=list)
    difficulty: str = "intermediate"
    domain: str = "software"
    active: bool = True


@router.post("/api/assessments/start")
def start(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    result = start_attempt(db, user.student_id)
    db.commit()
    return result


@router.post("/api/assessments/{attempt_id}/submit")
def submit(attempt_id: uuid.UUID, payload: SubmitAttempt, db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    result = submit_attempt(db, user.student_id, attempt_id, [item.model_dump() for item in payload.answers])
    db.commit()
    return result


@router.get("/api/assessments/latest")
def latest(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    result = latest_result(db, user.student_id)
    return {"result": result}


@router.get("/api/admin/questions")
def admin_questions(db: Session = Depends(get_db), user: CurrentUser = Depends(require_role("admin", "administrator"))):
    del user
    rows = list_questions_admin(db)
    db.commit()
    return {"questions": rows}


@router.post("/api/admin/questions", status_code=201)
def admin_create_question(payload: QuestionWrite, db: Session = Depends(get_db), user: CurrentUser = Depends(require_role("admin", "administrator"))):
    del user
    result = create_question(db, payload.model_dump())
    db.commit()
    return result


@router.put("/api/admin/questions/{question_id}")
def admin_update_question(question_id: uuid.UUID, payload: QuestionWrite, db: Session = Depends(get_db), user: CurrentUser = Depends(require_role("admin", "administrator"))):
    del user
    result = update_question(db, question_id, payload.model_dump())
    db.commit()
    return result


@router.delete("/api/admin/questions/{question_id}")
def admin_delete_question(question_id: uuid.UUID, db: Session = Depends(get_db), user: CurrentUser = Depends(require_role("admin", "administrator"))):
    del user
    delete_question(db, question_id)
    db.commit()
    return {"status": "deactivated"}
