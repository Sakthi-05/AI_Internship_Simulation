# Adaptive intelligence integration

The project now has a complete offline-capable adaptive loop. `GEMINI_API_KEY`
may be added later; when it is empty, the deterministic local provider is used.

## Flow

1. Module 1 / onboarding collects role, stack and an initial assessment response.
2. `/api/intelligence/assessment` creates a persistent competency baseline.
3. Module 2 starts the workflow simulation as before.
4. `/api/intelligence/simulations/{simulation_id}/adaptive/next` creates a student-specific workplace situation.
5. `/api/intelligence/simulations/{simulation_id}/adaptive/respond` evaluates the response, stores a trace, updates competencies, creates feedback/readiness data, and creates the next situation using the updated state.
6. Module 3 can consume `interaction_evaluations` for response-level scoring while its existing submission/code review remains intact.
7. Module 4 consumes the accumulated evidence through analytics, feedback, readiness and certificate endpoints.

## Important data objects

- `student_profiles`: baseline + current competency state.
- `simulation_interactions`: append-only trace with parent links.
- `interaction_evaluations`: response-level competency scoring.
- `feedback_reports`: generated performance summaries.
- `readiness_snapshots`: weighted recent performance + linear forecast.
- `early_warning_alerts`: three-session continuous decline detection.
- `certificates`: eligibility-controlled skill certificates.

## AI provider boundary

All AI work is provider-agnostic. The existing `AIService` abstraction remains
the provider boundary. Offline behavior is deterministic and does not require
network access or a key. Setting `GEMINI_API_KEY` switches the structured AI
operations to Gemini without changing API routes.
