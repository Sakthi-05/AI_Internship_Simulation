"""
Unit and integration tests for password hashing migration to bcrypt.

Covers:
- Bcrypt hash generation and format verification ($2b$ with 12 rounds)
- Successful verification with correct password
- Failure with incorrect password
- Safe handling of malformed, truncated, and corrupt hashes
- Safe handling of non-string and None inputs
- Backward compatibility with legacy PBKDF2-HMAC-SHA256 hashes
- Password length boundary handling (respecting bcrypt's 72-byte limit)
- Transparent rehashing of legacy accounts upon successful login
- Registration producing modern bcrypt hashes
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.core.passwords import (
    hash_password,
    hash_password_legacy,
    needs_rehash,
    verify_password,
)
from app.models.user import User
from app.services.auth_service import login, register_student


# ---------------------------------------------------------------------------
# Fake DB for testing auth_service
# ---------------------------------------------------------------------------


class _FakeScalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeAuthDB:
    def __init__(self, user: User | None = None):
        self.user = user
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    def execute(self, stmt) -> Any:
        return _FakeScalar(self.user)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def flush(self):
        self.flushes += 1

    def refresh(self, obj):
        pass


# ---------------------------------------------------------------------------
# Bcrypt hashing tests
# ---------------------------------------------------------------------------


def test_hash_password_generates_valid_bcrypt_format():
    hashed = hash_password("SuperSecret123!")
    assert isinstance(hashed, str)
    assert hashed.startswith("$2b$12$")
    assert len(hashed) == 60


def test_hash_password_generates_unique_salts():
    pw = "SamePasswordAcrossCalls"
    hash1 = hash_password(pw)
    hash2 = hash_password(pw)
    assert hash1 != hash2
    assert verify_password(pw, hash1) is True
    assert verify_password(pw, hash2) is True


def test_hash_password_rejects_non_string():
    with pytest.raises(TypeError):
        hash_password(123456)  # type: ignore
    with pytest.raises(TypeError):
        hash_password(None)  # type: ignore


# ---------------------------------------------------------------------------
# Verification tests: Bcrypt
# ---------------------------------------------------------------------------


def test_verify_password_bcrypt_success():
    pw = "P@ssword2026!"
    h = hash_password(pw)
    assert verify_password(pw, h) is True


def test_verify_password_bcrypt_incorrect_password():
    pw = "CorrectPassword"
    h = hash_password(pw)
    assert verify_password("IncorrectPassword", h) is False
    assert verify_password("", h) is False


# ---------------------------------------------------------------------------
# Backward compatibility: Legacy PBKDF2
# ---------------------------------------------------------------------------


def test_verify_password_legacy_pbkdf2_success():
    pw = "legacy-student-pass"
    leg_hash = hash_password_legacy(pw)
    assert leg_hash.startswith("pbkdf2_sha256$")
    assert verify_password(pw, leg_hash) is True


def test_verify_password_legacy_pbkdf2_incorrect_password():
    leg_hash = hash_password_legacy("real-password")
    assert verify_password("wrong-password", leg_hash) is False
    assert verify_password("", leg_hash) is False


def test_verify_password_legacy_pbkdf2_custom_iterations():
    pw = "test-custom-iter"
    leg_hash = hash_password_legacy(pw, iterations=50_000)
    assert leg_hash.startswith("pbkdf2_sha256$50000$")
    assert verify_password(pw, leg_hash) is True
    assert verify_password("wrong", leg_hash) is False


# ---------------------------------------------------------------------------
# Boundary & length safety (72-byte limit)
# ---------------------------------------------------------------------------


def test_bcrypt_long_password_boundary_safety():
    """Passwords longer than 72 bytes must not raise ValueError from bcrypt."""
    long_pw = "A" * 150
    h = hash_password(long_pw)
    assert h.startswith("$2b$12$")
    assert verify_password(long_pw, h) is True


def test_bcrypt_multibyte_utf8_long_password():
    """Multi-byte characters (e.g. 4-byte emojis) exceeding 72 bytes."""
    emoji_pw = "🔐🔑🚀🛡️" * 20  # Over 100 bytes of UTF-8
    h = hash_password(emoji_pw)
    assert verify_password(emoji_pw, h) is True
    assert verify_password("🔐🔑🚀🛡️", h) is False


def test_legacy_long_password_still_verifies():
    """Legacy PBKDF2 had no 72-byte limit; existing long passwords must verify."""
    long_pw = "B" * 100
    leg_hash = hash_password_legacy(long_pw)
    assert verify_password(long_pw, leg_hash) is True
    assert verify_password("B" * 99, leg_hash) is False


# ---------------------------------------------------------------------------
# Malformed and invalid input safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_hash",
    [
        "",
        "   ",
        "invalid_hash",
        "pbkdf2_sha256$",
        "pbkdf2_sha256$notanint$salt$digest",
        "pbkdf2_sha256$1000$",
        "pbkdf2_sha256$1000$salt",
        "pbkdf2_sha256$-5$salt$digest",
        "$2b$12$short",
        "$2b$",
        "$2b$notanint$invalid",
        "$unknown$hash$format",
        "plaintextpassword",
        None,
    ],
)
def test_verify_password_safe_with_malformed_hashes(bad_hash):
    assert verify_password("any_password", bad_hash) is False


@pytest.mark.parametrize(
    "bad_password",
    [
        None,
        123,
        ["pw"],
        {"password": "test"},
    ],
)
def test_verify_password_safe_with_non_string_passwords(bad_password):
    h = hash_password("valid_hash_source")
    assert verify_password(bad_password, h) is False  # type: ignore


# ---------------------------------------------------------------------------
# needs_rehash tests
# ---------------------------------------------------------------------------


def test_needs_rehash_returns_true_for_legacy_pbkdf2():
    leg_hash = hash_password_legacy("student-pass")
    assert needs_rehash(leg_hash) is True


def test_needs_rehash_returns_false_for_modern_bcrypt():
    modern_hash = hash_password("student-pass")
    assert needs_rehash(modern_hash) is False


def test_needs_rehash_returns_true_for_lower_work_factor():
    old_work_factor_hash = "$2b$08$abcdefghijklmnopqrstuvwxyz1234567890abcdefghijkl"
    assert needs_rehash(old_work_factor_hash) is True


def test_needs_rehash_returns_true_for_old_variant():
    old_variant_hash = "$2a$12$abcdefghijklmnopqrstuvwxyz1234567890abcdefghijkl"
    assert needs_rehash(old_variant_hash) is True


def test_needs_rehash_returns_false_for_empty_or_none():
    assert needs_rehash("") is False
    assert needs_rehash(None) is False
    assert needs_rehash("random-string") is False


# ---------------------------------------------------------------------------
# Transparent rehashing on login
# ---------------------------------------------------------------------------


def test_login_transparently_rehashes_legacy_pbkdf2_user(monkeypatch):
    legacy_pw = "OriginalP@ssword1"
    legacy_hash = hash_password_legacy(legacy_pw)

    user = User(
        email="legacy.student@example.com",
        password_hash=legacy_hash,
        full_name="Legacy Learner",
        role="student",
        student_id=str(uuid.uuid4()),
    )
    db = FakeAuthDB(user=user)

    # User logs in with original password
    result = login(db, email="legacy.student@example.com", password=legacy_pw)

    # 1. Login succeeds and returns bearer token
    assert "access_token" in result
    assert result["email"] == "legacy.student@example.com"

    # 2. Stored hash is transparently upgraded to bcrypt
    assert user.password_hash.startswith("$2b$12$")
    assert user.password_hash != legacy_hash

    # 3. New hash verifies with the same password
    assert verify_password(legacy_pw, user.password_hash) is True

    # 4. DB commit was triggered to persist the upgraded hash
    assert db.commits >= 1


def test_login_does_not_rehash_already_modern_bcrypt_user():
    modern_pw = "ModernP@ssword1"
    initial_hash = hash_password(modern_pw)

    user = User(
        email="modern.student@example.com",
        password_hash=initial_hash,
        full_name="Modern Learner",
        role="student",
        student_id=str(uuid.uuid4()),
    )
    db = FakeAuthDB(user=user)

    result = login(db, email="modern.student@example.com", password=modern_pw)
    assert "access_token" in result

    # Password hash was NOT modified
    assert user.password_hash == initial_hash
    # No extra commit for rehashing
    assert db.commits == 0


def test_login_failure_does_not_rehash_or_modify_stored_hash():
    legacy_pw = "RealPassword"
    legacy_hash = hash_password_legacy(legacy_pw)

    user = User(
        email="legacy.student@example.com",
        password_hash=legacy_hash,
        full_name="Legacy Learner",
        role="student",
        student_id=str(uuid.uuid4()),
    )
    db = FakeAuthDB(user=user)

    with pytest.raises(HTTPException) as exc:
        login(db, email="legacy.student@example.com", password="WrongPassword")

    assert exc.value.status_code == 401
    assert user.password_hash == legacy_hash
    assert db.commits == 0


def test_register_creates_bcrypt_hash(monkeypatch):
    """New registrations must immediately use bcrypt."""
    db = FakeAuthDB(user=None)  # No existing user with this email
    monkeypatch.setattr(
        "app.services.auth_service.get_or_create_profile",
        lambda _db, _sid: MagicMock(),
    )

    result = register_student(
        db,
        email="new.student@example.com",
        password="MyBrandNewPassword123!",
        full_name="New Learner",
    )

    assert "access_token" in result
    registered_user = db.added[0]
    assert registered_user.password_hash.startswith("$2b$12$")
    assert verify_password("MyBrandNewPassword123!", registered_user.password_hash) is True
