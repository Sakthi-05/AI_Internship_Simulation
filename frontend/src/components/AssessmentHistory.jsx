import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../api/client";
import EmptyState from "./EmptyState";
import {
  ASSESSMENT_HISTORY_PAGE_SIZE,
  assessmentDetailUrl,
  compareAssessmentCompetencies,
  displayHistoricalValue,
  getAssessmentHistoryPagination,
  parseAssessmentHistoryResponse,
  parseHistoricalResult,
} from "../lib/assessmentHistory";

function formatDate(value) {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleString();
}

function scoreLabel(value) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(2)}/100` : "Unavailable";
}

function AssessmentFeedback({ evaluation }) {
  const answers = Array.isArray(evaluation?.answers) ? evaluation.answers : [];
  if (!answers.length) return <p className="mt-2 text-xs text-text-faint">No answer feedback was retained for this attempt.</p>;
  return (
    <div className="mt-3 space-y-2">
      {answers.map((answer, index) => (
        <article key={answer.question_id || `${answer.competency || "answer"}-${index}`} className="rounded-xl border border-white/[0.06] bg-ink/40 p-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-text-faint">{answer.competency?.replaceAll("_", " ") || "Assessment question"}</p>
            <span className="chip font-mono">{scoreLabel(answer.score)}</span>
          </div>
          {answer.question && <p className="mt-2 text-sm text-text">{answer.question}</p>}
          {answer.response && <p className="mt-2 text-xs text-text-muted"><span className="font-semibold text-text">Your answer:</span> {answer.response}</p>}
          {answer.explanation && <p className="mt-2 text-xs text-text-muted">{answer.explanation}</p>}
          {answer.feedback && <p className="mt-1 text-xs text-text-muted">{answer.feedback}</p>}
        </article>
      ))}
    </div>
  );
}

function CompetencyComparison({ attempts }) {
  const rows = compareAssessmentCompetencies(attempts);
  if (!rows.length) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[34rem] text-left text-xs">
        <thead>
          <tr className="border-b border-white/[0.08] text-text-faint">
            <th className="py-2 pr-3 font-semibold">Competency</th>
            {attempts.map((attempt) => (
              <th key={attempt.attempt_id} className="px-2 py-2 font-medium">{formatDate(attempt.completed_at)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name} className="border-b border-white/[0.04] last:border-0">
              <th scope="row" className="py-2 pr-3 font-medium capitalize text-text-muted">{row.name.replaceAll("_", " ")}</th>
              {row.values.map((entry, index) => (
                <td key={`${row.name}-${attempts[index].attempt_id}`} className="px-2 py-2 font-mono text-text-muted">
                  {entry.label}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AssessmentHistory() {
  const [offset, setOffset] = useState(0);
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState(null);
  const [selectedAttemptId, setSelectedAttemptId] = useState(null);
  const [selectedResult, setSelectedResult] = useState(null);
  const [resultLoading, setResultLoading] = useState(false);
  const [resultError, setResultError] = useState(null);
  const firstSelectionMade = useRef(false);
  const historyRequest = useRef(0);
  const resultRequest = useRef(0);

  const loadHistory = useCallback(async (requestedOffset = offset) => {
    const requestId = ++historyRequest.current;
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const response = await apiClient.get("/api/assessments/history", {
        params: { limit: ASSESSMENT_HISTORY_PAGE_SIZE, offset: requestedOffset },
      });
      const page = parseAssessmentHistoryResponse(response.data);
      if (requestId !== historyRequest.current) return;
      setHistory(page);
      if (!firstSelectionMade.current && page.items.length > 0) {
        const newest = page.items[0];
        setSelectedAttemptId(newest.attempt_id);
        setSelectedResult(parseHistoricalResult(newest, newest.attempt_id));
        setResultError(null);
        firstSelectionMade.current = true;
      }
    } catch (error) {
      if (requestId === historyRequest.current) setHistoryError(error.message || "Assessment history could not be loaded.");
    } finally {
      if (requestId === historyRequest.current) setHistoryLoading(false);
    }
  }, [offset]);

  useEffect(() => {
    loadHistory(offset);
  }, [loadHistory, offset]);

  const selectAttempt = async (attemptId) => {
    setSelectedAttemptId(attemptId);
    setSelectedResult(null);
    setResultError(null);
    setResultLoading(true);
    const requestId = ++resultRequest.current;
    try {
      const response = await apiClient.get(assessmentDetailUrl(attemptId));
      const result = parseHistoricalResult(response.data, attemptId);
      if (requestId === resultRequest.current) setSelectedResult(result);
    } catch (error) {
      if (requestId === resultRequest.current) setResultError(error.message || "This assessment result could not be loaded.");
    } finally {
      if (requestId === resultRequest.current) setResultLoading(false);
    }
  };

  const retrySelectedResult = () => {
    if (selectedAttemptId) selectAttempt(selectedAttemptId);
  };

  const pagination = history ? getAssessmentHistoryPagination(history) : null;
  const comparisonAttempts = history?.items || [];
  const selectedCompetencyRows = selectedResult ? compareAssessmentCompetencies([selectedResult]) : [];

  return (
    <section className="card space-y-4 p-5" aria-labelledby="assessment-history-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Assessment history</p>
          <h2 id="assessment-history-heading" className="mt-1 font-display text-base font-semibold text-text">Previous baseline results</h2>
        </div>
        {history && <p className="text-xs text-text-faint">{history.total} completed {history.total === 1 ? "attempt" : "attempts"}</p>}
      </div>

      {historyError && (
        <div className="rounded-xl border border-status-blocked/25 bg-status-blocked/[0.06] p-3 text-xs text-status-blocked" role="alert">
          <span>History could not be loaded: {historyError}</span>
          <button type="button" className="btn-ghost ml-2" onClick={() => loadHistory(offset)}>Retry</button>
        </div>
      )}

      {historyLoading && !history && (
        <div className="flex items-center gap-2 py-5 text-xs text-text-faint" role="status">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand-400/30 border-t-brand-300" />
          Loading assessment history…
        </div>
      )}

      {history && !historyLoading && history.items.length === 0 && !historyError && (
        <EmptyState compact icon="◷" title="No previous assessments" hint="Completed baseline assessments will appear here." />
      )}

      {history?.items?.length > 0 && (
        <>
          <div className="grid gap-2 sm:grid-cols-2">
            {history.items.map((item) => {
              const selected = selectedAttemptId === item.attempt_id;
              return (
                <button
                  type="button"
                  key={item.attempt_id}
                  aria-pressed={selected}
                  onClick={() => selectAttempt(item.attempt_id)}
                  className={`rounded-xl border p-3 text-left transition-colors ${selected ? "border-brand-400/50 bg-brand-500/10" : "border-white/[0.06] bg-white/[0.02] hover:border-brand-400/30"}`}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-text">{formatDate(item.completed_at)}</span>
                    {selected && <span className="chip">Selected</span>}
                  </span>
                  <span className="mt-1 block text-xs text-text-muted">Overall score: {scoreLabel(item.overall_score)}</span>
                  <span className="mt-1 block text-[11px] text-text-faint">Historical data: {item.historical_data_status || "Unavailable"}</span>
                </button>
              );
            })}
          </div>

          {historyLoading && <p className="text-xs text-text-faint" role="status">Loading another page…</p>}

          <div className="border-t border-white/[0.07] pt-4">
            {resultLoading && <p className="py-3 text-xs text-text-faint" role="status">Loading selected assessment…</p>}
            {resultError && (
              <div className="rounded-xl border border-status-blocked/25 bg-status-blocked/[0.06] p-3 text-xs text-status-blocked" role="alert">
                <span>Selected result could not be loaded: {resultError}</span>
                <button type="button" className="btn-ghost ml-2" onClick={retrySelectedResult}>Retry</button>
              </div>
            )}
            {selectedResult && !resultLoading && (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="font-display text-sm font-semibold text-text">Selected assessment result</h3>
                  <span className="chip capitalize">{selectedResult.historical_data_status || "Historical status unavailable"}</span>
                </div>
                {selectedResult.historical_data_status === "legacy" && (
                  <p className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2 text-xs text-text-muted">
                    Some historical information may not have been retained for this assessment. Missing values are shown as unavailable.
                  </p>
                )}

                <div className="grid gap-2 sm:grid-cols-2">
                  <HistoricalField label="Overall score" value={scoreLabel(selectedResult.overall_score)} />
                  <HistoricalField label="Technical domain" value={selectedResult.technical_domain_id?.replaceAll("_", " ")} />
                  <HistoricalField label="Technology familiarity ratings" value={formatTechnologyRatings(selectedResult.technology_ratings)} />
                  <HistoricalField label="Recommended difficulty" value={selectedResult.recommended_difficulty} />
                  <HistoricalField label="Strengths" value={selectedResult.strengths} />
                  <HistoricalField label="Focus areas" value={selectedResult.focus_areas} />
                  <HistoricalField label="Recommendation" value={selectedResult.recommendation} />
                </div>

                <div>
                  <h4 className="text-[11px] font-semibold uppercase tracking-wider text-text-faint">Competency results</h4>
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    {selectedCompetencyRows.map((row) => (
                      <div key={row.name} className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2 text-xs">
                        <span className="capitalize text-text-muted">{row.name.replaceAll("_", " ")}</span>
                        <span className="font-mono text-text">{row.values[0].label}</span>
                      </div>
                    ))}
                    {selectedCompetencyRows.length === 0 && <p className="text-xs text-text-faint">Unavailable</p>}
                  </div>
                  {(selectedResult.unassessed_competencies || []).length > 0 && (
                    <p className="mt-2 text-xs text-text-faint">Not assessed: {selectedResult.unassessed_competencies.map((name) => name.replaceAll("_", " ")).join(", ")}</p>
                  )}
                </div>

                <div>
                  <h4 className="text-[11px] font-semibold uppercase tracking-wider text-text-faint">Answer feedback</h4>
                  <AssessmentFeedback evaluation={selectedResult.evaluation} />
                </div>
              </div>
            )}
          </div>

          {comparisonAttempts.length > 1 && (
            <div className="border-t border-white/[0.07] pt-4">
              <h3 className="font-display text-sm font-semibold text-text">Competency comparison</h3>
              <p className="mt-1 text-xs text-text-faint">Scores are compared across the attempts on this page. Missing values are not treated as zero.</p>
              <div className="mt-2"><CompetencyComparison attempts={comparisonAttempts} /></div>
            </div>
          )}

          {pagination && history.total > history.limit && (
            <div className="flex items-center justify-between border-t border-white/[0.07] pt-3">
              <p className="text-xs text-text-faint">Showing {history.offset + 1}–{Math.min(history.offset + history.items.length, history.total)} of {history.total}</p>
              <div className="flex gap-2">
                <button type="button" className="btn-ghost" disabled={!pagination.canPrevious || historyLoading} onClick={() => setOffset(pagination.previousOffset)}>Previous</button>
                <button type="button" className="btn-ghost" disabled={!pagination.canNext || historyLoading} onClick={() => setOffset(pagination.nextOffset)}>Next</button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function HistoricalField({ label, value }) {
  return (
    <div className="rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-text-faint">{label}</p>
      <p className="mt-1 text-xs text-text-muted">{displayHistoricalValue(value)}</p>
    </div>
  );
}

function formatTechnologyRatings(ratings) {
  if (!Array.isArray(ratings)) return "Unavailable";
  if (!ratings.length) return "None recorded";
  return ratings.map((rating) => {
    if (!rating || typeof rating.technology_id !== "string") return null;
    const value = Number.isInteger(rating.self_rating) ? `${rating.self_rating}/5` : "Unavailable";
    return `${rating.technology_id.replaceAll("_", " ")}: ${value}`;
  }).filter(Boolean).join(" · ") || "Unavailable";
}
