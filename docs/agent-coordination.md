# Agent coordination

The simulation has bounded, workflow-owned handoffs. `AgentOrchestrator` validates one request and one typed result at a time; business services and API routes continue to own authorization, persistence, and state transitions. There is no global pipeline and no automatic retry layer.

## Handoffs

| Agent | Trigger and dependency | Coordination behavior |
| --- | --- | --- |
| Scenario | A caller has a concrete workflow activity and persisted trigger evidence. | Independent proposal. It returns `not_proposed` when eligibility/context checks fail. It does not create a workplace event. |
| Manager | A persisted student message or workplace event requires a manager response. | Invoked by the chat/simulation route for that interaction through the application-owned orchestrator, with DB/provider dependencies bound per request. The existing communication service owns persistence and broadcasts. |
| Technical Review | An authenticated owner submits an eligible current submission version. | Synchronous request because its validated result feeds the existing review state transition. Duplicate version requests reuse the existing review claim/result. |
| Competency | A scoped analytics request or eligible adaptation handoff needs a persisted evidence assessment. | Synchronous, deterministic consolidation; no scoring write. Analytics computes once and passes the typed result to Career Insights. Adaptation requests its own task-scoped assessment only after a completed review and valid submission provenance. |
| Adaptation | A completed technical review has an eligible next task and provenance. | Runs after the technical adaptation service. It proposes focus only; service/task generation decides whether and how to apply it. Failures retain baseline task generation. |
| Career Insights | Simulation-scoped analytics is requested. | Runs after competency assessment, reuses that result and existing analytics recommendations/readiness. Its failure does not fail analytics. |

Scenario and manager proposals do not depend on review or competency. Scenario eligibility is implemented, but no current route invokes the Scenario Agent; a future caller must connect it to a concrete persisted activity before invoking it. The existing requirement-change workflow is already triggered and persisted by `workplace_scenarios` and owns criteria/version transitions; the proposal-only Scenario Agent cannot replace or safely augment that transition today. Review, competency, and adaptation are not forced into an unrelated sequence. No agent is called from a workflow lacking its trigger condition.

## Reliability

The orchestrator enforces request timeouts, strict input/output models, correlation IDs, bounded public errors, and per-handoff status logs. It does not retry: review/provider calls and manager communication can have side effects. Callers own fallbacks: analytics emits an unavailable optional field, review preserves a retryable failed review state, and adaptation leaves existing task generation in place. Missing evidence stays missing/insufficient; it is not converted to a score.

The technical review, manager, competency, adaptation, and career-insights calls use the application-owned registry/orchestrator. Manager DB/provider dependencies are bound with `ContextVar` only around each request. The manager response cache is keyed by simulation and persisted trigger identity. Context building filters student and simulation ownership; agents verify those identities and evidence references before using them.

## Refresh and frontend

Persisted business changes continue to use existing WebSocket/workspace update events. Intelligence also refreshes on visibility recovery and rejects stale request results. Agent responses are read-only analytics and do not emit workflow events.
