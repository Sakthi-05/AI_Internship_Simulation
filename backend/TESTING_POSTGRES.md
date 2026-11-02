# Safe PostgreSQL test setup

The root pytest fixtures create tables and drop them during teardown. They are
guarded to run only against the local PostgreSQL database named
aiwse_chunk10_test. They refuse to use DATABASE_URL as an implicit
fallback, refuse any other database name or non-local host, and check the
connected database and schema again before teardown.

## One-time administrator setup

The configured aiwse role cannot create databases or schemas in the existing
PostgreSQL instance. Have a PostgreSQL administrator run this once:

```sql
CREATE DATABASE aiwse_chunk10_test OWNER aiwse;
```

This creates a new database and does not alter aiwse_db or
aiwse_test_db. Verify it as the test role:

```sql
SELECT current_database(),
       current_user,
       has_database_privilege(current_user, current_database(), 'CREATE');
```

Connect to aiwse_chunk10_test as aiwse; the result must name
aiwse_chunk10_test and report true for the CREATE privilege.

## Apply migrations and run tests

In PowerShell from the repository root, set TEST_DATABASE_URL to the new
database using the existing local test role credentials. The URL must use the
local PostgreSQL host and the exact database name above.

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://aiwse:<password>@localhost:5432/aiwse_chunk10_test"
$env:DATABASE_URL = $env:TEST_DATABASE_URL
Push-Location backend
.\venv\Scripts\python.exe -m alembic upgrade head
Pop-Location
.\backend\venv\Scripts\python.exe -m pytest -q
```

Do not point either variable at the application database. The test fixture
requires TEST_DATABASE_URL even when DATABASE_URL is present.
