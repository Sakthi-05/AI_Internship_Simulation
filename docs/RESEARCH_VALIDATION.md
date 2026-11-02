# Research validation: evidence-driven internship simulation

## Research problem and motivation

Technical internship practice is often modeled as a fixed task sequence, while real workplace work can change after a task begins. Assessment can also become difficult to audit when a competency label is detached from its underlying submission, review, interaction, learner, simulation, and task. This project addresses those engineering problems with a workflow simulator that persists activity/evidence, validates contextual ownership, and uses bounded agents to propose or explain decisions.

## Proposed contribution

The proposed framing is **“An Evidence-Driven Multi-Agent Framework for Adaptive Technical Internship Simulation under Dynamic Workplace Requirements.”** The contribution should be treated as a system design hypothesis, not a novelty claim. Candidate mechanisms:

1. Requirement-change-aware task workflow, with persisted criteria versions and student response/actions.
2. Evidence-provenance-based competency consolidation over existing assessment, interaction, review, and workplace evidence.
3. Competency-aware follow-up task-focus proposals grounded in recorded evidence and subordinate to existing task/progression services.
4. Conditional, typed agent handoffs with scoped context, correlation IDs, and failure containment.

The named “base paper” was not present in the repository or supplied with this task. A direct claim-by-claim comparison to that paper is unresolved. Related literature already describes evidence-centered design in a multi-agent conversation assessment architecture ([Springer/AIED 2025 chapter](https://doi.org/10.1007/978-3-031-98417-4_9)), a skill-driven assessment-centered agentic learning pattern ([Learning in Blocks, PMLR 2026](https://proceedings.mlr.press/v339/scaria26a.html)), and multi-agent learning interactions over a formal curriculum ([Computers & Education 2025](https://doi.org/10.1016/j.compedu.2025.105472)). These are related work, not a substitute for the missing base paper. Merely using multiple agents is not a novelty claim. A defensible contribution may lie in combining dynamic software-workflow requirements, provenance-preserving competency evidence, guarded follow-up task focus, and application-owned service handoffs, subject to comparison with the actual base paper and empirical evaluation.

## Architecture and responsibility boundaries

- **Scenario Agent:** proposal-only eligibility and evidence-grounded scenario proposal. No current route invokes it.
- **Manager Agent:** response proposal for persisted messages/events; application-owned registry with request-bound session/provider. Communication persistence and WebSocket push remain in existing routes/services.
- **Technical Review Agent:** validates current submission and criteria provenance and delegates evaluation to the existing review service; review persistence/state transitions remain service-owned.
- **Competency Agent:** deterministic consolidation of persisted evidence; no new scoring or persistence.
- **Adaptation Agent:** proposes supported competency task focus after completed review and eligible follow-up context; existing adaptation/task services own progression.
- **Career Insights Agent:** consumes the current Competency Agent response and existing analytics recommendation/readiness outputs; read-only.
- **Orchestrator:** validates typed inputs/outputs, timeouts, correlation metadata, and safe public errors. It does not retry potentially non-idempotent operations.

The active requirement-change path is `workplace_scenarios`, which creates and persists workflow events and criteria versions from workplace/task activity. It owns duplicate prevention and criteria/state transitions. The proposal-only Scenario Agent cannot safely replace that workflow. Its current triggers have no route handoff that preserves this ownership and avoids duplicate events, so it remains uninvoked.

## Experimental methodology

No historical baseline cohort, student outcome dataset, or specified base paper was found in the repository. No paid provider calls or learner study were conducted. The reproducible harness `backend/scripts/research_validation_harness.py` compares five fixed synthetic fixtures per experiment. It is a **policy/contract conformance smoke harness**, not execution of four complete application deployments and not empirical evidence about learners. Baseline/proposed semantics are explicit in the script. Re-run with:

```powershell
backend\venv\Scripts\python.exe backend/scripts/research_validation_harness.py
```

| Experiment | Independent variable | Controlled inputs | Outcome |
| --- | --- | --- | --- |
| A: dynamic requirements | Static original requirement vs evolving active requirement | Same five old/new requirement pairs | Whether active work specification contains the new requirement |
| B: provenance | Conventional score-only result vs score linked to persisted evidence reference | Same four scored synthetic dimensions in five cases; one case has no score | Traceability coverage and count of scores without references |
| C: adaptation | Technical-performance-only progression vs supported competency-focus proposal | Same five cases; four contain one named supported gap, one has no score | Correct competency task-focus match; withholding when evidence is absent |
| D: coordination | Independent unconditional invocations vs conditional reuse/handoff | Same five fixtures: one review failure, two preexisting assessments, two competency handoffs | Call count, duplicate competency calls avoided, downstream calls skipped |

These are fixed-input comparisons; there is no randomization, inferential statistic, human rating, or simulated latency/token charge. “Success,” “traceability,” and “match” mean only the script predicates, not valid assessment, learning gain, or production reliability.

## Actual harness results

Executed `backend\venv\Scripts\python.exe backend/scripts/research_validation_harness.py` once; five fixtures per experiment (20 fixture evaluations total):

| Experiment | Baseline result | Proposed result | Interpretation boundary |
| --- | --- | --- | --- |
| A | 0/5 new requirements present | 5/5 present | Fixed requirement-string rule only |
| B | 0/4 dimensions traceable; 4 unreferenced scores | 4/4 traceable; 0 unreferenced scores | Synthetic pointer-presence check only |
| C | 0/4 eligible gap matches | 4/4 eligible gap matches; 1/5 case withheld for missing score | Simple target-match oracle, not learner benefit |
| D | 15 modeled calls | 11 modeled calls; 2 preexisting competency calls reused; 2 downstream calls skipped after review failure | Hand-coded call-policy fixture, not production latency/cost |

The harness does not measure assessment consistency, rework, completion, actual agent failure rates, latency, or provider cost. Do not report its numbers as experimental performance results.

## Verification status and workflow matrix

| Student journey stage | Owner / route or service | Agent involvement | Persisted evidence/state | Verification status in this run |
| --- | --- | --- | --- | --- |
| Registration/authentication | Auth/student routes and security dependencies | None | Student/auth records | Source inspected; database/API journey not run |
| Company/project generation | `POST /api/generate` | Existing generation/provider services | Company, project, sprint, tasks, simulation linkage | Source inspected; route tests in root DB suite not run |
| Simulation/task assignment | Simulation routes and task path services | None | Simulation, task paths, current task | Source inspected; route tests in root DB suite not run |
| Submission | Simulation/submission routes and submission service | None | Submission/version and task state | Source inspected; route tests in root DB suite not run |
| Technical review | `POST /api/projects/{project_id}/reviews/versions/{version_id}` | Technical Review Agent | Review result and existing task progression | Focused agent suite passed; DB journey not run |
| Manager interaction | Company manager chat and simulation event response routes | Manager Agent via shared app orchestrator; request-bound DB/provider | Messages/event metadata; route persists and broadcasts | Manager and route unit tests in backend suite; live API not run |
| Workplace scenario / requirement change | Workplace event and `workplace_scenarios` service | Scenario Agent not invoked; Manager Agent may respond | WorkflowEvent, criteria versions, soft-skill observations | Existing requirement-change tests are in root DB suite and were not run |
| Competency assessment | `GET /api/intelligence/analytics?simulation_id=...` | Competency Agent | Existing assessment/review/interaction evidence references | Focused analytics/competency tests passed |
| Competency-aware adaptation | Review service follow-up focus handoff | Adaptation Agent, with task-scoped Competency assessment | Existing review/adaptation metadata and generated task focus | Focused adaptation/review tests passed; DB progression not run |
| Follow-up/progression | Existing task generation, adaptation, progression services | Agent proposes focus only | Task/path/progression state | Unit tests passed; full DB journey not run |
| Career insights/analytics | Simulation-scoped Intelligence analytics | Career Insights reuses Competency output | Existing recommendation/readiness values and referenced history | Focused analytics and career agent tests passed |
| Dashboard refresh | Intelligence page and workspace refresh hooks | None | No additional persisted state | Frontend production build and lint passed; browser not run |
| WebSocket/UI synchronization | Existing company WebSocket manager and workspace refresh mapping | None | Existing event/message/task updates | Client event-mapping tests passed; live delivery not rerun |

## Verification commands and results

Executed during this run:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/test_manager_agent.py backend/tests/test_agent_orchestration.py -q
backend\venv\Scripts\python.exe -m pytest backend/tests/test_manager_agent.py backend/tests/test_agent_orchestration.py backend/tests/test_competency_analytics_route.py backend/tests/test_adaptation_agent.py backend/tests/test_career_insights_agent.py backend/tests/test_scenario_agent.py -q
backend\venv\Scripts\python.exe -m pytest backend/tests -q
backend\venv\Scripts\python.exe backend/scripts/research_validation_harness.py
cd frontend; npm run build
cd frontend; npm run lint
cd frontend; node --test src/lib/workspaceSync.test.js
```

Results: focused agent/workflow tests (manager, orchestrator, analytics, adaptation, career, scenario) **67 passed**; full backend suite **129 passed**; harness **5 fixed cases per experiment**; workspace/WebSocket mapping tests **2 passed**; frontend build succeeded; lint completed with existing warnings in AuthContext, SimulationContext, Chat, and Board. Backend pytest reports existing `pytest-asyncio` configuration and `python_multipart` deprecation warnings. The first manager/orchestrator-only check also passed (23 tests before the additional shared-registry test was added).

Before attempting destructive database fixtures, the test configuration was inspected. Root `tests/conftest.py` requires `TEST_DATABASE_URL` to be PostgreSQL psycopg on localhost, database exactly `aiwse_chunk10_test`, schema `public`; it rechecks the connected database, creates tables, and drops them at teardown. Here `TEST_DATABASE_URL` is unset and `DATABASE_URL` is unset. No `psql`/`pg_isready` command was available; querying Windows service state was denied by the environment. Root database integration/E2E tests were not run. No database connection was attempted; no production or development database was contacted, reset, or migrated. Historical statements in `docs/VERIFICATION.md` describe earlier sessions and were not independently revalidated here.

## Limitations and threats to validity

- No named base paper was available, so direct comparison and novelty assessment remain provisional.
- Synthetic fixtures encode expected answers and can only establish that selected rules produce those answers.
- No students, instructors, or independent reviewers participated; there is no evidence of learning gain, competency validity, usability, fairness, or workplace realism.
- Requirement changes and rubric changes can be confounded with task difficulty, prior skill, and time-on-task.
- Evidence coverage is not evidence validity. A reference proves provenance linkage, not that the artifact supports the competency claim.
- Agent/provider behavior varies by model, prompt, and runtime; live provider behavior, latency, and token/API cost were not measured.
- Manager shared-registry reuse was tested with request-bound DB/provider doubles; live concurrency was not exercised.
- Scenario Agent remains uninvoked because the current event persistence path owns requirement-change generation and no safe proposal-only handoff was found.
- Database/API/browser/live WebSocket integration was not performed in this run.

## Next steps before project submission

1. Supply the intended base paper and map its claims to this implementation.
2. Provision the exact disposable local PostgreSQL database `aiwse_chunk10_test`, set `TEST_DATABASE_URL`, and run the root DB/API/E2E suite after confirming the fixture guard.
3. Run a browser journey and live WebSocket assertion against that isolated environment.
4. Add a safe Scenario Agent caller only if a real persisted trigger can be handed off without duplicating requirement-change persistence.
5. Preregister a learner study with blinded rubric scoring, comparison conditions, sample-size rationale, and measures for validity, transfer, rework, completion, and equity before claiming educational effectiveness.
