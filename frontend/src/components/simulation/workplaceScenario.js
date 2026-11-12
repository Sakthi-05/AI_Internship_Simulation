/** UI state for the requirement-change workplace scenario. No timers and no scores are invented here. */

export const FUTURE_SCENARIOS = [
  "collaboration",
  "time_management",
  "prioritization",
  "ownership",
  "feedback_response",
];

export function activeRequirementChange(events) {
  const changes = (events || []).filter((event) => event?.event_type === "requirement_change");
  return changes.find((event) => event.status === "pending") || changes[changes.length - 1] || null;
}

export function workplacePhase(event) {
  if (!event || event.event_type !== "requirement_change") return "idle";
  if (event.status === "pending") return "acknowledge";
  if (!event.response) return "reply";
  const described = (event.subsequent_actions || []).some((item) => item.action === "adapted_work");
  if (!described) return "adapt";
  return "verify";
}

export function popupRequired(event) {
  return workplacePhase(event) === "acknowledge";
}

export function restoredPhase(event) {
  return workplacePhase(JSON.parse(JSON.stringify(event)));
}

export function observationLabel(observation) {
  if (!observation || observation.status !== "scored" || observation.score == null) {
    return "insufficient evidence";
  }
  return `${Math.round(observation.score)}`;
}
