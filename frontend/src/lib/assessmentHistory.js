export const ASSESSMENT_HISTORY_PAGE_SIZE = 20;

const isRecord = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

export function parseAssessmentHistoryResponse(data) {
  if (!isRecord(data) || !Array.isArray(data.items)) {
    throw new Error("Assessment history returned an unexpected response.");
  }

  const items = data.items.filter((item) => isRecord(item) && typeof item.attempt_id === "string" && item.attempt_id.length > 0);
  const total = Number.isInteger(data.total) && data.total >= 0 ? data.total : items.length;
  const limit = Number.isInteger(data.limit) && data.limit > 0 ? data.limit : ASSESSMENT_HISTORY_PAGE_SIZE;
  const offset = Number.isInteger(data.offset) && data.offset >= 0 ? data.offset : 0;
  const hasMore = typeof data.has_more === "boolean" ? data.has_more : offset + items.length < total;

  return { items, total, limit, offset, hasMore };
}

export function parseHistoricalResult(data, expectedAttemptId) {
  if (!isRecord(data) || typeof data.attempt_id !== "string" || !data.attempt_id) {
    throw new Error("Assessment result returned an unexpected response.");
  }
  if (expectedAttemptId && data.attempt_id !== expectedAttemptId) {
    throw new Error("The returned assessment does not match the selected attempt.");
  }
  // Keep this projection independent of profile state; absent historical fields stay absent/null.
  return data;
}

export function assessmentDetailUrl(attemptId) {
  if (typeof attemptId !== "string" || !attemptId.trim()) {
    throw new Error("A valid assessment attempt ID is required.");
  }
  return `/api/assessments/${encodeURIComponent(attemptId)}`;
}

export function getAssessmentHistoryPagination({ total, limit, offset, hasMore }) {
  return {
    canPrevious: offset > 0,
    canNext: hasMore && offset + limit < total,
    previousOffset: Math.max(0, offset - limit),
    nextOffset: offset + limit,
  };
}

export function compareAssessmentCompetencies(attempts) {
  const competencies = new Set();
  for (const attempt of attempts) {
    if (isRecord(attempt?.competencies)) {
      Object.keys(attempt.competencies).forEach((name) => competencies.add(name));
    }
    if (Array.isArray(attempt?.unassessed_competencies)) {
      attempt.unassessed_competencies.filter((name) => typeof name === "string").forEach((name) => competencies.add(name));
    }
  }

  return [...competencies].sort().map((name) => ({
    name,
    values: attempts.map((attempt) => {
      if (Array.isArray(attempt?.unassessed_competencies) && attempt.unassessed_competencies.includes(name)) {
        return { status: "not-assessed", label: "Not assessed" };
      }
      const score = attempt?.competencies?.[name];
      if (typeof score === "number" && Number.isFinite(score)) {
        return { status: "scored", value: score, label: `${score.toFixed(1)}/100` };
      }
      return { status: "unavailable", label: "Unavailable" };
    }),
  }));
}

export function displayHistoricalValue(value, emptyLabel = "Unavailable") {
  if (value === null || value === undefined || value === "") return emptyLabel;
  if (Array.isArray(value)) return value.length ? value.join(", ") : "None recorded";
  if (isRecord(value)) return Object.entries(value).map(([key, item]) => `${key.replaceAll("_", " ")}: ${Array.isArray(item) ? item.join(", ") : item}`).join(" · ") || emptyLabel;
  return String(value);
}
