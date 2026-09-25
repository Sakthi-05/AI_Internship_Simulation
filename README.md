# AI Internship Workflow Simulation — Integrated Project

This repository is the integrated local version of the AI Internship Simulation
project. It combines the existing workflow simulation engine with the adaptive
AI intelligence layer and Module 4 output/analytics layer.

## What is included

- Student onboarding/profile + AI baseline competency assessment.
- Company/project/sprint/task workflow simulation.
- AI project manager and teammate interactions.
- Workflow events, meetings, notifications, activity log, timeline and WebSockets.
- Adaptive AI workplace situations driven by student responses.
- Response-level competency evaluation and traceable interaction history.
- Module 4: feedback, analytics, industry-readiness forecasting, early-warning alerts, certificates and admin dashboard.
- Offline deterministic AI when no API key is configured.
- Optional Gemini provider through `GEMINI_API_KEY`.

## No API key is included

`GEMINI_API_KEY` is intentionally empty. Add your key to `backend/.env` later
if you want live Gemini calls.

## Recommended local run

### Option A — Docker for PostgreSQL only

From `docker/` run `docker compose up db`.
Then use the local backend/frontend scripts.

### Option B — Existing local PostgreSQL

Create a database matching `DATABASE_URL` in `backend/.env`, then:

```text
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```text
cd frontend
npm install
copy .env.example .env
npm run dev
```

Frontend: http://localhost:5173
API: http://localhost:8000
Swagger: http://localhost:8000/docs

PowerShell helpers are in `scripts/`.

## Local auth

Students register and sign in at `/api/auth/register` and `/api/auth/login`.
The issued JWT is the same token the simulation APIs already validate.

Development can still mint a token with `/api/dev/token`. The onboarding screen
keeps that path under "Development sign-in". The route is disabled when
`ENVIRONMENT` is not `development`.

After sign-in, a student completes a profile, rates skills from the catalog,
and takes the baseline assessment. The result recommends the role, stack,
difficulty, and company type used to start the internship.

## Adaptive demo

1. Sign in as a student.
2. Generate an internship.
3. Start the simulation.
4. Open the Adaptive AI panel.
5. Submit a reasoned decision.
6. The backend evaluates the response, updates the student's competency state,
   stores a trace, and immediately generates the next situation.
7. Open Intelligence to see the evolving competency profile, performance trace,
   feedback and readiness forecast.

See `docs/integration/ADAPTIVE_INTELLIGENCE.md` for the data flow.

> **Python 3.14 / Windows:** This project uses `pydantic>=2.12` and `psycopg-binary` so pip can install prebuilt CPython 3.14 wheels instead of compiling `pydantic-core` locally.
