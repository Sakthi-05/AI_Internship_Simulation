"""Module 1 authentication."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import CurrentUser, get_current_user
from app.services.auth_service import get_user_by_student_id, login, register_student

router = APIRouter(prefix="/api/auth", tags=["Module 1 — Authentication"])


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


@router.post("/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    if "@" not in payload.email:
        from fastapi import HTTPException

        raise HTTPException(400, "Enter a valid email address")
    result = register_student(db, email=payload.email, password=payload.password, full_name=payload.full_name)
    db.commit()
    return result


@router.post("/login")
def login_route(payload: LoginRequest, db: Session = Depends(get_db)):
    return login(db, email=payload.email, password=payload.password)


@router.get("/me")
def me(db: Session = Depends(get_db), user: CurrentUser = Depends(get_current_user)):
    account = get_user_by_student_id(db, user.student_id)
    return {
        "student_id": user.student_id,
        "role": user.role,
        "email": account.email if account else user.raw_claims.get("email"),
        "full_name": account.full_name if account else user.raw_claims.get("full_name"),
    }
