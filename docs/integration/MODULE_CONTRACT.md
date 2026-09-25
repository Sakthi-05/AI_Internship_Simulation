# Four-module integration contract

## Module 1 — Student Management

Produces a student identity, academic profile, skill ratings, assessment
evidence, baseline competencies, and the simulation parameters Module 2 consumes.

Authentication:

- `POST /api/auth/register` — email, password, full name. Issues a JWT whose `sub` is `student_id`.
- `POST /api/auth/login`
- `GET /api/auth/me`

Profile and skills:

- `GET /api/students/me`
- `PUT /api/students/me` — name, college, degree, year, career goal, role, industry
- `GET /api/skills`
- `PUT /api/students/me/skills` — catalog skills with a 1–5 self rating

Assessment and recommendation:

- `POST /api/assessments/start`
- `POST /api/assessments/{attempt_id}/submit`
- `GET /api/assessments/latest`
- `GET /api/students/me/recommendation` — available after the baseline assessment

The recommendation is stored on `student_profiles.recommended_parameters`:

```json
{
  "role": "Backend Developer",
  "technology_stack": ["Python", "FastAPI"],
  "difficulty": "intermediate",
  "company_type": "startup",
  "focus_competencies": ["debugging", "communication"],
  "career_goal": "Backend internship"
}
```

`POST /api/generate` still accepts those fields. When the student has a completed
baseline assessment, the profile's `recommended_parameters` replace the posted
role, stack, difficulty, and company type. Weak competencies become `[Focus]`
tasks and a strong competency can add a `[Stretch]` task. Both stay on the
student's task path at every difficulty. The response includes `simulation_id`
for a simulation that is ready to start.

Question bank (admin):

- `GET /api/admin/questions`
- `POST /api/admin/questions`
- `PUT /api/admin/questions/{question_id}`
- `DELETE /api/admin/questions/{question_id}` — deactivates the question

`/api/dev/token` remains available when `ENVIRONMENT=development`.

## Module 2 — Workflow Simulation

Owns company, project, sprint, task, workflow event, meeting, notification,
chat and simulation state. Every simulation interaction is appended to
`simulation_interactions`. Starting a task records a task-started notification
and a manager check-in the student can answer on the workplace events panel.

Adaptive extension:
- `GET /api/intelligence/simulations/{simulation_id}/adaptive/next`
- `POST /api/intelligence/simulations/{simulation_id}/adaptive/respond`
- `GET /api/intelligence/simulations/{simulation_id}/adaptive/history`

## Module 3 — AI Evaluation

Existing submission/code review remains the technical evaluation path.
Adaptive response evaluations are stored in `interaction_evaluations` so
Module 3 can consume them without overloading the code-review tables.

## Module 4 — Feedback, readiness and administration

Consumes student profiles, simulation interactions and evaluation records.
Primary endpoints:
- `GET /api/intelligence/analytics`
- `GET /api/intelligence/feedback`
- `POST /api/intelligence/feedback/generate`
- `GET /api/intelligence/readiness`
- `POST /api/intelligence/readiness/recalculate`
- `GET /api/intelligence/certificates`
- `POST /api/intelligence/certificates/issue`
- `GET /api/intelligence/admin/students` (admin role)

## Shared identifiers

Every cross-module record should carry:

- `student_id`
- `simulation_id` where applicable
- `scenario_id` where applicable
- `interaction_id` where applicable
- `task_id` where applicable

## AI provider contract

The application uses the existing `AIService` abstraction. Empty
`GEMINI_API_KEY` means local deterministic AI. A non-empty key enables Gemini.
No module should call the provider directly outside the AI service boundary.
