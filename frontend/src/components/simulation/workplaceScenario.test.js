import assert from "node:assert/strict";
import test from "node:test";
import {
  FUTURE_SCENARIOS,
  activeRequirementChange,
  observationLabel,
  popupRequired,
  restoredPhase,
  workplacePhase,
} from "./workplaceScenario.js";

const pending = {
  id: "event-1",
  event_type: "requirement_change",
  status: "pending",
  response: null,
  subsequent_actions: [],
  original_requirement: "Returns JSON",
  updated_requirement: "Validate invalid input",
};

test("a pending requirement change must be acknowledged and survives refresh", () => {
  assert.equal(popupRequired(pending), true);
  assert.equal(workplacePhase(pending), "acknowledge");
  assert.equal(restoredPhase(pending), "acknowledge");
  assert.equal(activeRequirementChange([pending, { event_type: "stakeholder_message", status: "pending" }]).id, "event-1");
});

test("acknowledgment moves the student to a free-text reply, then an adaptation", () => {
  const acknowledged = { ...pending, status: "acknowledged", manager_message: "Please adapt the work." };
  assert.equal(popupRequired(acknowledged), false);
  assert.equal(workplacePhase(acknowledged), "reply");
  const replied = { ...acknowledged, response: "I will update validation for invalid input." };
  assert.equal(workplacePhase(replied), "adapt");
  const submittedOnly = {
    ...replied,
    subsequent_actions: [{ action: "task_submitted" }],
    assessment: { adaptability: { status: "scored", score: 74 } },
  };
  assert.equal(workplacePhase(submittedOnly), "adapt");
  const adapted = { ...replied, subsequent_actions: [{ action: "adapted_work" }] };
  assert.equal(workplacePhase(adapted), "assessed");
  assert.equal(restoredPhase(adapted), "assessed");
});

test("missing evidence is labeled instead of shown as a score", () => {
  assert.equal(observationLabel({ status: "insufficient_evidence", score: null }), "insufficient evidence");
  assert.equal(observationLabel({ status: "scored", score: 82 }), "82");
  assert.equal(observationLabel(null), "insufficient evidence");
});

test("future workplace scenarios stay unimplemented in this flow", () => {
  assert.deepEqual(FUTURE_SCENARIOS, [
    "collaboration",
    "time_management",
    "prioritization",
    "ownership",
    "feedback_response",
  ]);
  assert.equal(FUTURE_SCENARIOS.includes("requirement_change"), false);
});
