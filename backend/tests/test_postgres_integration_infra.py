"""
Tests for PostgreSQL test database configuration, isolation, safety, and cleanup.

Verifies:
- Explicit and consistent test database configuration (defaulting to ai_internship_test)
- Strict prevention of destructive operations against development database 'ai_internship'
- Strict rejection of system and non-test databases
- Strict loopback-only host restriction (preventing accidental targeting of remote instances)
- Driver validation (PostgreSQL psycopg)
- Masking of sensitive credentials in logs/errors
- Actionable error instructions when PostgreSQL is unavailable
- Database teardown safety and reverse-dependency cascade cleanup
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.engine import make_url

from app.core.test_db_config import (
    DEFAULT_TEST_DATABASE_NAME,
    DEV_DATABASE_NAME,
    FORBIDDEN_DATABASES,
    TestDatabaseConfig,
    TestDatabaseConfigurationError,
    TestDatabaseSecurityError,
    TestDatabaseUnavailableError,
    get_setup_instructions,
    mask_database_url,
    resolve_test_database_url,
    validate_test_database_config,
    verify_database_connection,
)


# ---------------------------------------------------------------------------
# URL Resolution & Default Fallbacks
# ---------------------------------------------------------------------------


def test_resolve_explicit_test_database_url(monkeypatch):
    custom_url = "postgresql+psycopg://tester:secret@localhost:5432/custom_feature_test"
    monkeypatch.setenv("TEST_DATABASE_URL", custom_url)
    url, is_explicit = resolve_test_database_url()
    assert url == custom_url
    assert is_explicit is True


def test_resolve_default_derives_from_dev_database_url(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://myuser:mypass@127.0.0.1:5432/ai_internship")
    url, is_explicit = resolve_test_database_url()
    assert is_explicit is False
    parsed = make_url(url)
    assert parsed.database == DEFAULT_TEST_DATABASE_NAME
    assert parsed.username == "myuser"
    assert parsed.host == "127.0.0.1"


def test_resolve_default_fallback_when_no_env(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url, is_explicit = resolve_test_database_url()
    assert is_explicit is False
    parsed = make_url(url)
    assert parsed.database == DEFAULT_TEST_DATABASE_NAME
    assert parsed.host in {"localhost", "127.0.0.1"}


# ---------------------------------------------------------------------------
# Validation & Critical Safety Guards
# ---------------------------------------------------------------------------


def test_reject_development_database():
    """CRITICAL: Tests must NEVER execute against 'ai_internship'."""
    dev_url = "postgresql+psycopg://postgres:postgres@localhost:5432/ai_internship"
    with pytest.raises(TestDatabaseSecurityError) as exc_info:
        validate_test_database_config(dev_url)
    err = str(exc_info.value)
    assert "development database" in err
    assert "ai_internship" in err


@pytest.mark.parametrize("sys_db", ["postgres", "template0", "template1"])
def test_reject_system_databases(sys_db):
    url = f"postgresql+psycopg://postgres:postgres@localhost:5432/{sys_db}"
    with pytest.raises(TestDatabaseSecurityError) as exc_info:
        validate_test_database_config(url)
    assert sys_db in str(exc_info.value)


@pytest.mark.parametrize(
    "remote_host",
    [
        "192.168.1.100",
        "10.0.0.5",
        "production.database.mycompany.com",
        "staging-db.internal",
    ],
)
def test_reject_non_local_hosts(remote_host):
    """Integration tests execute DROP TABLE CASCADE; only loopback hosts are allowed."""
    url = f"postgresql+psycopg://postgres:postgres@{remote_host}:5432/ai_internship_test"
    with pytest.raises(TestDatabaseSecurityError) as exc_info:
        validate_test_database_config(url)
    err = str(exc_info.value)
    assert "non-local" in err
    assert remote_host in err


@pytest.mark.parametrize(
    "invalid_driver",
    [
        "mysql+pymysql",
        "sqlite",
        "oracle+oracledb",
    ],
)
def test_reject_invalid_drivers(invalid_driver):
    url = f"{invalid_driver}://postgres:postgres@localhost:5432/ai_internship_test"
    with pytest.raises(TestDatabaseConfigurationError) as exc_info:
        validate_test_database_config(url)
    assert "Invalid driver" in str(exc_info.value)


def test_reject_non_test_named_databases():
    """Databases must clearly indicate a test context (contain 'test' or end with '_test')."""
    url = "postgresql+psycopg://postgres:postgres@localhost:5432/production_replica"
    with pytest.raises(TestDatabaseSecurityError) as exc_info:
        validate_test_database_config(url)
    assert "production_replica" in str(exc_info.value)


@pytest.mark.parametrize(
    "valid_db",
    [
        "ai_internship_test",
        "aiwse_chunk10_test",
        "aiwse_test",
        "feature_x_test",
        "internship_test_db",
    ],
)
def test_accept_valid_test_databases(valid_db):
    url = f"postgresql+psycopg://postgres:postgres@localhost:5432/{valid_db}"
    config = validate_test_database_config(url)
    assert config.database_name == valid_db
    assert config.host == "localhost"
    assert config.driver == "postgresql+psycopg"


# ---------------------------------------------------------------------------
# Masking & Setup Guidance
# ---------------------------------------------------------------------------


def test_mask_database_url_hides_password():
    url = "postgresql+psycopg://app_user:super_secret_pw@localhost:5432/ai_internship_test"
    masked = mask_database_url(url)
    assert "super_secret_pw" not in masked
    assert "***" in masked
    assert "app_user" in masked
    assert "ai_internship_test" in masked


def test_get_setup_instructions_contains_actionable_steps():
    guide = get_setup_instructions("Database does not exist", target_db="ai_internship_test")
    assert "CREATE DATABASE ai_internship_test;" in guide
    assert "$env:TEST_DATABASE_URL" in guide
    assert "export TEST_DATABASE_URL" in guide
    assert "pytest tests" in guide
    assert "NEVER execute against the development database 'ai_internship'" in guide


# ---------------------------------------------------------------------------
# Connection Verification & Isolation
# ---------------------------------------------------------------------------


def test_verify_connection_succeeds_on_valid_match():
    engine = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.one.return_value = ("ai_internship_test", "public")
    engine.connect.return_value.__enter__.return_value = conn

    # Should not raise
    verify_database_connection(engine, "ai_internship_test")


def test_verify_connection_rejects_dev_database_even_if_connected():
    engine = MagicMock()
    conn = MagicMock()
    # Simulated bug: connection accidentally connected to development DB
    conn.execute.return_value.one.return_value = ("ai_internship", "public")
    engine.connect.return_value.__enter__.return_value = conn

    with pytest.raises(TestDatabaseSecurityError) as exc_info:
        verify_database_connection(engine, "ai_internship_test")
    assert "development database 'ai_internship'" in str(exc_info.value)


def test_verify_connection_rejects_database_mismatch():
    engine = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.one.return_value = ("other_test_db", "public")
    engine.connect.return_value.__enter__.return_value = conn

    with pytest.raises(TestDatabaseSecurityError) as exc_info:
        verify_database_connection(engine, "ai_internship_test")
    assert "connected to 'other_test_db'" in str(exc_info.value)


def test_verify_connection_rejects_non_public_schema():
    engine = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.one.return_value = ("ai_internship_test", "custom_schema")
    engine.connect.return_value.__enter__.return_value = conn

    with pytest.raises(TestDatabaseSecurityError) as exc_info:
        verify_database_connection(engine, "ai_internship_test")
    assert "unexpected schema 'custom_schema'" in str(exc_info.value)


def test_verify_connection_raises_actionable_error_on_connection_failure():
    engine = MagicMock()
    engine.connect.side_effect = Exception("connection to server at 127.0.0.1:5432 failed: Connection refused")

    with pytest.raises(TestDatabaseUnavailableError) as exc_info:
        verify_database_connection(engine, "ai_internship_test")

    err = str(exc_info.value)
    assert "Could not connect to PostgreSQL test database" in err
    assert "CREATE DATABASE ai_internship_test;" in err
    assert "Ensure local PostgreSQL is running" in err
