"""
PostgreSQL test database configuration and safety verification.

Enforces:
1. Explicit/consistent test database configuration (defaulting to 'ai_internship_test').
2. Strict isolation from the development database 'ai_internship' and system databases.
3. Loopback-only host restriction (preventing accidental targeting of remote instances).
4. Actionable error reporting and setup guidance when PostgreSQL is unavailable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from sqlalchemy.engine import make_url
from sqlalchemy import text


DEV_DATABASE_NAME = "ai_internship"
DEFAULT_TEST_DATABASE_NAME = "ai_internship_test"

# Names explicitly recognized as test databases
ALLOWED_TEST_DB_NAMES = {
    DEFAULT_TEST_DATABASE_NAME,
    "aiwse_chunk10_test",
    "aiwse_test",
}

FORBIDDEN_DATABASES = {
    DEV_DATABASE_NAME,
    "postgres",
    "template0",
    "template1",
}

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
ALLOWED_DRIVERS = {"postgresql+psycopg", "postgresql"}


class TestDatabaseConfigurationError(RuntimeError):
    """Raised when test database configuration is missing, malformed, or invalid."""
    __test__ = False


class TestDatabaseSecurityError(TestDatabaseConfigurationError):
    """Raised when a destructive operation would target a non-test or non-local database."""
    __test__ = False


class TestDatabaseUnavailableError(TestDatabaseConfigurationError):
    """Raised when the target PostgreSQL test database cannot be reached."""
    __test__ = False


@dataclass(frozen=True)
class TestDatabaseConfig:
    __test__ = False
    url: str
    database_name: str
    host: str
    driver: str
    user: Optional[str] = None


def mask_database_url(raw_url: str) -> str:
    """Mask the password in a connection string for safe display."""
    try:
        parsed = make_url(raw_url)
        if parsed.password:
            return str(parsed._replace(password="***"))
        return str(parsed)
    except Exception:
        return "<invalid-url>"


def get_setup_instructions(reason: str = "", target_db: str = DEFAULT_TEST_DATABASE_NAME, url: str = "") -> str:
    """Format clear, step-by-step setup instructions for PostgreSQL test database."""
    lines = [
        "=" * 80,
        "POSTGRESQL INTEGRATION TEST DATABASE REQUIRED",
        "=" * 80,
    ]
    if reason:
        lines.append(f"Issue: {reason}")
    if url:
        lines.append(f"Target URL: {mask_database_url(url)}")
    lines.extend([
        "",
        "The integration test suite executes real PostgreSQL database operations",
        "and requires a dedicated, disposable local test database.",
        "",
        "SAFETY NOTE:",
        f"  Tests will NEVER execute against the development database '{DEV_DATABASE_NAME}'.",
        f"  A dedicated test database (e.g. '{target_db}') is required.",
        "",
        "SETUP INSTRUCTIONS:",
        "1. Ensure local PostgreSQL is running on port 5432.",
        f"2. Create the dedicated test database:",
        f"     psql -U postgres -c \"CREATE DATABASE {target_db};\"",
        "3. Configure the test database connection:",
        "   - Windows PowerShell:",
        f"       $env:TEST_DATABASE_URL = \"postgresql+psycopg://postgres:postgres@localhost:5432/{target_db}\"",
        "   - Linux / macOS Bash:",
        f"       export TEST_DATABASE_URL=\"postgresql+psycopg://postgres:postgres@localhost:5432/{target_db}\"",
        "4. Run the integration test suite:",
        "     pytest tests",
        "=" * 80,
    ])
    return "\n".join(lines)


def resolve_test_database_url() -> tuple[str, bool]:
    """Resolve the test database URL from environment or fallback default.

    Returns:
        tuple[url, is_explicit]: (connection string, True if explicitly set via TEST_DATABASE_URL).
    """
    explicit_url = os.environ.get("TEST_DATABASE_URL")
    if explicit_url and explicit_url.strip():
        return explicit_url.strip(), True

    # Fallback to deriving from DATABASE_URL or .env if available
    base_url = os.environ.get("DATABASE_URL")
    if not base_url:
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        if env_path.is_file():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                        base_url = line.split("=", 1)[1].strip()
                        break
            except Exception:
                pass

    if base_url:
        try:
            parsed = make_url(base_url)
            # Safely replace the database name with DEFAULT_TEST_DATABASE_NAME
            derived = parsed._replace(database=DEFAULT_TEST_DATABASE_NAME)
            return str(derived), False
        except Exception:
            pass

    # Default fallback URL for local development
    default_url = f"postgresql+psycopg://postgres:postgres@localhost:5432/{DEFAULT_TEST_DATABASE_NAME}"
    return default_url, False


def validate_test_database_config(url: str, is_explicit: bool = True) -> TestDatabaseConfig:
    """Validate that the test database URL complies with safety and environment requirements.

    Raises:
        TestDatabaseSecurityError: If pointing to a development, system, or remote database.
        TestDatabaseConfigurationError: If driver or URL syntax is invalid.
    """
    if not url or not isinstance(url, str):
        raise TestDatabaseConfigurationError(
            get_setup_instructions("TEST_DATABASE_URL is not set or empty.")
        )

    try:
        parsed = make_url(url.strip())
    except Exception as exc:
        raise TestDatabaseConfigurationError(
            get_setup_instructions(f"Malformed database URL: {exc}", url=url)
        )

    db_name = parsed.database or ""
    host = parsed.host or "localhost"
    driver = parsed.drivername

    # 1. Driver validation
    if driver not in ALLOWED_DRIVERS:
        raise TestDatabaseConfigurationError(
            f"Invalid driver '{driver}'. TEST_DATABASE_URL must use the PostgreSQL psycopg driver "
            f"(e.g. postgresql+psycopg://...)."
        )

    # 2. Host validation (loopback only)
    if host not in ALLOWED_HOSTS:
        raise TestDatabaseSecurityError(
            f"Refusing destructive test fixtures against non-local host '{host}'. "
            f"Integration tests must run against a local PostgreSQL instance (localhost, 127.0.0.1, or ::1)."
        )

    # 3. Database isolation validation
    if db_name == DEV_DATABASE_NAME:
        raise TestDatabaseSecurityError(
            get_setup_instructions(
                f"CRITICAL: Refusing to run tests against development database '{DEV_DATABASE_NAME}'! "
                "Tests execute destructive operations (DROP TABLE CASCADE).",
                target_db=DEFAULT_TEST_DATABASE_NAME,
                url=url,
            )
        )

    if db_name in FORBIDDEN_DATABASES:
        raise TestDatabaseSecurityError(
            f"Refusing destructive test fixtures for forbidden system database '{db_name}'."
        )

    # Must contain 'test' or end with '_test' or match explicitly allowed names
    is_valid_test_name = (
        db_name in ALLOWED_TEST_DB_NAMES
        or db_name.endswith("_test")
        or "test" in db_name
    )
    if not is_valid_test_name:
        raise TestDatabaseSecurityError(
            f"Refusing database '{db_name}'. Test database name must contain 'test' or end with '_test' "
            f"(e.g. '{DEFAULT_TEST_DATABASE_NAME}')."
        )

    return TestDatabaseConfig(
        url=parsed.render_as_string(hide_password=False),
        database_name=db_name,
        host=host,
        driver=driver,
        user=parsed.username,
    )


def verify_database_connection(engine, expected_db_name: str) -> None:
    """Connect to database, verify name and schema, and raise actionable instructions if unreachable."""
    try:
        with engine.connect() as conn:
            actual_db, schema = conn.execute(
                text("SELECT current_database(), current_schema()")
            ).one()
    except Exception as exc:
        raise TestDatabaseUnavailableError(
            get_setup_instructions(
                f"Could not connect to PostgreSQL test database: {exc}",
                target_db=expected_db_name,
            )
        )

    if actual_db == DEV_DATABASE_NAME:
        raise TestDatabaseSecurityError(
            f"CRITICAL: Connected to development database '{DEV_DATABASE_NAME}' instead of test database!"
        )

    if actual_db != expected_db_name:
        raise TestDatabaseSecurityError(
            f"Refusing destructive test fixtures: connected to '{actual_db}', expected '{expected_db_name}'."
        )

    if schema != "public":
        raise TestDatabaseSecurityError(
            f"Refusing destructive test fixtures: unexpected schema '{schema}', expected 'public'."
        )
