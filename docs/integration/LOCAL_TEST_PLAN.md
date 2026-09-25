# Local test plan

## Student flow

- Sign in with a dev student ID.
- Generate an internship.
- The onboarding assessment creates a competency baseline.
- Start the simulation.
- Use the Adaptive AI panel in the Simulation workspace.
- Submit a detailed decision.
- Confirm the next situation changes and is linked to the previous interaction.
- Open Intelligence and confirm score history, competency profile, feedback and readiness update.

## Dynamic proof

Use two student IDs and deliberately give different response styles. The offline
AI uses the response content and student/simulation identifiers to produce
stable but different scores and branches.

## Decline alert

For a controlled demo, seed or create three decreasing evaluated interactions
and call `/api/intelligence/readiness/recalculate`. An `OPEN` declining-
performance alert is created after three consecutive decreases.

## Admin flow

Mint a dev token with role `admin`, then open `/admin`. The endpoint is protected
by the role claim and aggregates student profiles.
