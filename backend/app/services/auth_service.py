"""Register and log in local student accounts."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.passwords import hash_password, verify_password, needs_rehash
from app.core.security import create_access_token
from app.models.user import User, new_student_id
from app.services.intelligence_service import get_or_create_profile


def _token_payload(user: User) -> Dict[str, Any]:
    return {
        "access_token": create_access_token(
            student_id=user.student_id,
            role=user.role,
            email=user.email,
            full_name=user.full_name,
        ),
        "token_type": "bearer",
        "student_id": user.student_id,
        "role": user.role,
        "email": user.email,
        "full_name": user.full_name,
    }


def register_student(db: Session, *, email: str, password: str, full_name: str) -> Dict[str, Any]:
    normalized = email.strip().lower()
    existing = db.execute(select(User).where(User.email == normalized)).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    student_id = new_student_id()
    user = User(
        email=normalized,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        role="student",
        student_id=student_id,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    profile = get_or_create_profile(db, student_id)
    profile.full_name = user.full_name
    db.add(profile)
    db.flush()
    return _token_payload(user)


def login(db: Session, *, email: str, password: str) -> Dict[str, Any]:
    user = db.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    # Transparently rehash legacy PBKDF2 or outdated hashes upon successful authentication
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.add(user)
        try:
            db.commit()
            if hasattr(db, "refresh"):
                db.refresh(user)
        except Exception:
            if hasattr(db, "rollback"):
                db.rollback()

    return _token_payload(user)


def get_user_by_student_id(db: Session, student_id: str) -> User | None:
    return db.execute(select(User).where(User.student_id == student_id)).scalar_one_or_none()
