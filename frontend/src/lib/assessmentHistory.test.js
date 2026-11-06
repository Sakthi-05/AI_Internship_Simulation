import assert from "node:assert/strict";
import test from "node:test";
import {
  ASSESSMENT_HISTORY_PAGE_SIZE,
  assessmentDetailUrl,
  compareAssessmentCompetencies,
  displayHistoricalValue,
  getAssessmentHistoryPagination,
  parseAssessmentHistoryResponse,
  parseHistoricalResult,
} from "./assessmentHistory.js";

test("parses the history response and filters malformed attempt entries", () => {
  const page = parseAssessmentHistoryResponse({
    items: [{ attempt_id: "newest", overall_score: 90 }, null, { overall_score: 70 }],
    total: 21,
    limit: 20,
    offset: 0,
    has_more: true,
  });
  assert.deepEqual(page, {
    items: [{ attempt_id: "newest", overall_score: 90 }],
    total: 21,
    limit: 20,
    offset: 0,
    hasMore: true,
  });
  assert.equal(ASSESSMENT_HISTORY_PAGE_SIZE, 20);
});

test("rejects unexpected history envelopes and normalizes malformed pagination metadata", () => {
  assert.throws(() => parseAssessmentHistoryResponse({ result: [] }), /unexpected response/);
  assert.deepEqual(parseAssessmentHistoryResponse({
    items: [{ attempt_id: "a" }], total: "many", limit: 0, offset: -1,
  }), {
    items: [{ attempt_id: "a" }], total: 1, limit: 20, offset: 0, hasMore: false,
  });
});

test("builds a correctly encoded detail URL and validates attempt IDs", () => {
  assert.equal(assessmentDetailUrl("attempt/one"), "/api/assessments/attempt%2Fone");
  assert.throws(() => assessmentDetailUrl(" "), /valid assessment attempt ID/);
  assert.throws(() => assessmentDetailUrl(null), /valid assessment attempt ID/);
});

test("pagination controls follow the backend page metadata", () => {
  assert.deepEqual(getAssessmentHistoryPagination({ total: 45, limit: 20, offset: 20, hasMore: true }), {
    canPrevious: true, canNext: true, previousOffset: 0, nextOffset: 40,
  });
  assert.deepEqual(getAssessmentHistoryPagination({ total: 45, limit: 20, offset: 40, hasMore: false }), {
    canPrevious: true, canNext: false, previousOffset: 20, nextOffset: 60,
  });
});

test("competency comparison distinguishes measured, not-assessed, and unavailable values", () => {
  const rows = compareAssessmentCompetencies([
    { attempt_id: "first", competencies: { technical_knowledge: 75 }, unassessed_competencies: ["adaptability"] },
    { attempt_id: "second", competencies: { adaptability: 50 }, unassessed_competencies: [] },
    { attempt_id: "legacy", competencies: {}, unassessed_competencies: null },
  ]);
  const byName = Object.fromEntries(rows.map((row) => [row.name, row.values]));
  assert.deepEqual(byName.technical_knowledge.map((cell) => cell.label), ["75.0/100", "Unavailable", "Unavailable"]);
  assert.deepEqual(byName.adaptability.map((cell) => cell.label), ["Not assessed", "50.0/100", "Unavailable"]);
  assert.equal(compareAssessmentCompetencies([{ competencies: { problem_solving: "0" } }])[0].values[0].label, "Unavailable");
});

test("legacy missing historical fields stay unavailable and do not use profile values", () => {
  const legacy = parseHistoricalResult({
    attempt_id: "legacy-attempt",
    historical_data_status: "legacy",
    technical_domain_id: null,
    technology_ratings: null,
    strengths: null,
    focus_areas: null,
    recommended_difficulty: null,
    recommendation: null,
  }, "legacy-attempt");
  const currentProfile = {
    technical_domain_id: "software_development",
    strengths: ["problem_solving"],
    recommended_difficulty: "advanced",
  };
  assert.equal(legacy.technical_domain_id, null);
  assert.equal(displayHistoricalValue(legacy.technical_domain_id), "Unavailable");
  assert.equal(displayHistoricalValue(legacy.strengths), "Unavailable");
  assert.equal(displayHistoricalValue(legacy.recommended_difficulty), "Unavailable");
  assert.notEqual(legacy.technical_domain_id, currentProfile.technical_domain_id);
  assert.equal(legacy.strengths, null);
});

test("rejects malformed or mismatched detail responses", () => {
  assert.throws(() => parseHistoricalResult(null, "expected"), /unexpected response/);
  assert.throws(() => parseHistoricalResult({ attempt_id: "other" }, "expected"), /does not match/);
});
