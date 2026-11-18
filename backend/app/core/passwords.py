"""Password hashing and verification with bcrypt and legacy PBKDF2 migration support.

Modern hashes use bcrypt ($2b$ with 12 rounds).
Legacy hashes (pbkdf2_sha256$<iterations>$<salt>$<hex digest>) remain supported
for authentication and are transparently rehashed to bcrypt on successful login.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

import bcrypt

_BCRYPT_ROUNDS = 12
_BCRYPT_MAX_BYTES = 72
_LEGACY_PREFIX = "pbkdf2_sha256$"
_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2x$", "$2y$")
_LEGACY_ITERATIONS = 120_000


def _prepare_password_bytes(password: str) -> bytes:
    """Encode password to UTF-8 and truncate to 72 bytes to respect bcrypt limits."""
    if not isinstance(password, str):
        raise TypeError("Password must be a string")
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Hash password using bcrypt with standard 12 rounds."""
    pw_bytes = _prepare_password_bytes(password)
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(pw_bytes, salt)
    return hashed.decode("utf-8")


def hash_password_legacy(password: str, iterations: int = _LEGACY_ITERATIONS) -> str:
    """Create a legacy PBKDF2-HMAC-SHA256 hash.

    Used for backward compatibility testing and reference.
    """
    if not isinstance(password, str):
        raise TypeError("Password must be a string")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return f"{_LEGACY_PREFIX}{iterations}${salt}${digest.hex()}"


def _verify_pbkdf2(password: str, stored: str) -> bool:
    """Verify password against legacy PBKDF2-HMAC-SHA256 stored hash."""
    try:
        parts = stored.split("$", 3)
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        if iterations <= 0:
            return False
        salt = parts[2].encode("utf-8")
        digest = parts[3]
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(candidate.hex(), digest)
    except Exception:
        return False


def _verify_bcrypt(password: str, stored: str) -> bool:
    """Verify password against bcrypt stored hash."""
    try:
        pw_bytes = _prepare_password_bytes(password)
        stored_bytes = stored.encode("utf-8")
        return bcrypt.checkpw(pw_bytes, stored_bytes)
    except Exception:
        return False


def verify_password(password: Optional[str], stored: Optional[str]) -> bool:
    """Verify password against stored hash (bcrypt or legacy PBKDF2).

    Safely returns False on malformed hashes or unexpected input without raising.
    """
    if not isinstance(password, str) or not isinstance(stored, str) or not stored:
        return False

    if stored.startswith(_LEGACY_PREFIX):
        return _verify_pbkdf2(password, stored)

    if stored.startswith(_BCRYPT_PREFIXES):
        return _verify_bcrypt(password, stored)

    return False


def needs_rehash(stored: Optional[str]) -> bool:
    """Check whether a stored password hash should be upgraded to modern bcrypt.

    Returns True for:
    - Legacy PBKDF2 hashes
    - Bcrypt hashes with outdated variant or lower work factor (< 12 rounds)
    """
    if not isinstance(stored, str) or not stored:
        return False

    # Legacy PBKDF2 hashes must always be rehashed
    if stored.startswith(_LEGACY_PREFIX):
        return True

    # Bcrypt hashes should use $2b$ and current rounds
    if stored.startswith(_BCRYPT_PREFIXES):
        if not stored.startswith("$2b$"):
            return True
        try:
            parts = stored.split("$")
            if len(parts) >= 3:
                rounds = int(parts[2])
                if rounds < _BCRYPT_ROUNDS:
                    return True
        except (ValueError, IndexError):
            return True
        return False

    # Unknown or malformed hash
    return False
