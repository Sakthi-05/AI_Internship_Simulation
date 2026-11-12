import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { criteriaLines, criteriaStateLabel, earlierCriteria } from "./criteriaDisplay";
import SubmissionModal from "./SubmissionModal";
import CodeEditor from "./CodeEditor";
import DiffViewer from "./DiffViewer";

const CODING_TASK_TYPES = ["coding", "debugging", "database", "api_development", "testing"];
const CODE_REVIEW_TYPES = ["debugging"];

export default function TaskPanel({
  task,
  scenario,
  simulation,
  project,
  onStart,
  onSubmit,
  onRetryReview,
  onComplete,
  showSubmissionModal,
  setShowSubmissionModal,
  isStarting,
  isSubmitting,
  isCompleting,
  canStart,
  canSubmit,
}) {
  const [remainingTime, setRemainingTime] = useState(task?.remaining_time);
  const [deadlineMissed, setDeadlineMissed] = useState(task?.deadline_missed);
  const [editorCode, setEditorCode] = useState("");
  const [hasEditorWork, setHasEditorWork] = useState(false);

  // Merge the CodeEditor draft into submission metadata so code counts as work evidence.
  const handleSubmitWithCode = (files, metadata) => {
    const merged = { ...(metadata || {}) };
    if (editorCode?.trim() && !merged.code) {
      merged.code = editorCode;
    }
    return onSubmit(files, merged);
  };



  useEffect(() => {
    if (!task?.started_at || !task?.deadline) {
      setRemainingTime(null);
      return;
    }

    const updateTimer = () => {
      const now = new Date();
      const deadline = new Date(task.deadline);
      const diffMs = deadline - now;

      if (diffMs <= 0) {
        setRemainingTime({
          seconds: 0,
          minutes: 0,
          hours: 0,
          formatted: "Expired",
        });
        setDeadlineMissed(true);
        return;
      }

      const hours = Math.floor(diffMs / 3600000);
      const minutes = Math.floor((diffMs % 3600000) / 60000);
      const seconds = Math.floor((diffMs % 60000) / 1000);

      let formatted = "";
      if (hours > 0) formatted = `${hours}h ${minutes}m ${seconds}s`;
      else if (minutes > 0) formatted = `${minutes}m ${seconds}s`;
      else formatted = `${seconds}s`;

      if (minutes < 5 && hours === 0) {
        formatted = `Warning: ${formatted}`;
      }

      setRemainingTime({
        seconds,
        minutes,
        hours,
        formatted,
      });
      setDeadlineMissed(false);
    };

    updateTimer();
    const interval = setInterval(updateTimer, 1000);
    return () => clearInterval(interval);
  }, [task?.deadline, task?.started_at]);

  useEffect(() => {
    if (task?.remaining_time) {
      setRemainingTime(task.remaining_time);
    }
    setDeadlineMissed(task?.deadline_missed || false);
  }, [task?.remaining_time, task?.deadline_missed]);

  if (!task) {
    return (
      <div className="card flex min-h-64 items-center justify-center p-6 text-center">
        <div><p className="text-sm font-semibold text-text">No active task</p><p className="mt-1 text-xs text-text-muted">The next task will appear here when it is available.</p></div>
      </div>
    );
  }

  const statusColors = {
    backlog: "bg-surface-2 text-text-muted",
    todo: "bg-status-todo/10 text-status-todo border-status-todo",
    in_progress: "bg-status-todo/10 text-status-todo border-status-todo",
    submitted: "bg-status-review/10 text-status-review border-status-review",
    under_review: "bg-status-review/10 text-status-review border-status-review",
    completed: "bg-status-completed/10 text-status-completed border-status-completed",
    changes_requested: "bg-status-progress/10 text-status-progress border-status-progress/30",
    blocked: "bg-status-progress/10 text-status-progress border-status-progress/30",
  };

  const statusLabels = {
    backlog: "Backlog",
    todo: "Ready to Start",
    in_progress: "In Progress",
    submitted: "Submitted",
    under_review: "Under Review",
    completed: "Completed",
    changes_requested: "Changes Requested",
    blocked: "Blocked",
  };

  const timeWarning = !deadlineMissed && Boolean(remainingTime && remainingTime.minutes < 5 && remainingTime.hours === 0);
  const canEditWork = canSubmit || task.status === "changes_requested";

  return (
    <div className="card simulation-task-panel flex min-w-0 flex-col overflow-hidden">
      <div className="simulation-task-header border-b p-5 sm:p-6">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <p className="eyebrow mb-2">Current task</p>
            <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
              <span className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold ${statusColors[task.status]}`}>
                {statusLabels[task.status] || task.status}
              </span>
              {task.difficulty && (
                <span className="chip text-[11px]! capitalize">
                  {task.difficulty}
                </span>
              )}
              {task.task_type && (
                <span className="chip text-[11px]!">
                  {task.task_type.replace("_", " ")}
                </span>
              )}
            </div>
            <h2 className="font-display text-xl font-bold leading-snug sm:text-2xl">{task.title}</h2>
          </div>

          <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">
            {remainingTime && (
              <div className={`deadline-pill ${deadlineMissed ? "deadline-expired" : timeWarning ? "deadline-warning" : "deadline-active"}`}>
                <span className="deadline-dot"/>
                <span>{deadlineMissed ? "Expired" : timeWarning ? "Due soon" : "Time left"}</span>
                <strong>{deadlineMissed ? "Deadline missed" : remainingTime.formatted.replace(/^Warning:\s*/, "")}</strong>
              </div>
            )}
            {!remainingTime && task.deadline && !task.started_at && (
              <div className="deadline-pill deadline-planned">
                <span className="deadline-dot"/><span>Estimate</span>
                <strong>{task.estimated_hours}h</strong>
              </div>
            )}
          </div>
        </div>

        <div className="task-meta-row flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-text-muted">
          {task.started_at && (
            <span><b>Started</b> {new Date(task.started_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
          )}
          {task.deadline && (
            <span className={deadlineMissed ? "text-status-blocked" : timeWarning ? "text-status-progress" : ""}><b>Deadline</b> {new Date(task.deadline).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span>
          )}
          {task.estimated_hours && (
            <span><b>Estimate</b> {task.estimated_hours}h</span>
          )}
        </div>
      </div>

      <div className="simulation-task-body relative min-w-0 space-y-6 p-5 sm:p-6">
        <section>
          <h3 className="mb-3 flex items-center gap-2 font-display text-xs font-bold uppercase tracking-[0.14em] text-text-faint">
            <span className="flex h-5 w-5 items-center justify-center rounded-md bg-brand-500/15 text-[11px] text-brand-300">✎</span>
            Instructions
          </h3>
          <div className="task-instructions rounded-xl border p-4 text-sm leading-7 text-text-muted">
            {(task.instructions || task.description || "No additional instructions are available for this task.").split("\n").map((paragraph, i) => (
              <p key={i} className="mb-2 last:mb-0">{paragraph}</p>
            ))}
          </div>
        </section>

        {(task.deliverables || task.task_metadata?.deliverables || []).length > 0 && (
          <section>
            <h3 className="mb-3 flex items-center gap-2 font-display text-xs font-bold uppercase tracking-[0.14em] text-text-faint">
              <span className="flex h-5 w-5 items-center justify-center rounded-md bg-accent-cyan/15 text-[11px] text-accent-cyan">↗</span>
              Expected deliverables
            </h3>
            <ul className="list-inside list-disc space-y-2 rounded-xl border p-4 text-sm text-text-muted">
              {(task.deliverables || task.task_metadata?.deliverables || []).map((item, index) => (
                <li key={`${index}-${item}`}>{typeof item === "string" ? item : item?.name || item?.description || JSON.stringify(item)}</li>
              ))}
            </ul>
          </section>
        )}

        {criteriaLines(task).length > 0 && (
          <section>
            <h3 className="mb-3 flex items-center gap-2 font-display text-xs font-bold uppercase tracking-[0.14em] text-text-faint">
              <span className="flex h-5 w-5 items-center justify-center rounded-md bg-status-completed/15 text-[11px] text-status-completed">✓</span>
              Acceptance criteria
              {task.criteria_version?.active_version_number ? (
                <span className="normal-case tracking-normal text-text-faint">version {task.criteria_version.active_version_number}</span>
              ) : null}
            </h3>
            <ul className="space-y-2">
              {criteriaLines(task).map((criterion, i) => (
                <motion.li
                  key={i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: Math.min(i * 0.05, 0.3) }}
                  className="acceptance-criterion flex items-start gap-2.5 rounded-lg border p-3 transition-colors hover:border-status-completed/30 hover:bg-status-completed/[0.04]"
                >
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-status-completed/10 text-[11px] font-bold text-status-completed">✓</span>
                  <span className="text-sm text-text">
                    {criterion.text}
                    {task.criteria_version ? (
                      <span className="ml-2 text-xs uppercase tracking-wide text-text-faint">{criteriaStateLabel(criterion.state)}</span>
                    ) : null}
                  </span>
                </motion.li>
              ))}
            </ul>
            {earlierCriteria(task).length > 0 && (
              <div className="mt-3 space-y-2">
                {earlierCriteria(task).map((version) => (
                  <div key={version.versionNumber} className="rounded-lg border border-dashed p-3 text-sm text-text-muted">
                    <p className="mb-1 text-xs font-bold uppercase tracking-wide text-text-faint">Version {version.versionNumber} · Superseded</p>
                    <ul className="list-inside list-disc">
                      {version.items.map((text) => <li key={text}>{text}</li>)}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {CODING_TASK_TYPES.includes(task.task_type) && task.status !== "completed" && (
          <section className="space-y-3">
            <div>
              <h3 className="font-display text-sm font-semibold uppercase tracking-wide text-status-todo">
                Your Work
              </h3>
                <p className="mt-1 text-sm text-text-muted">{canEditWork ? "Write or revise your solution here. Your editor draft is saved for this task and included when you submit." : "Start the task to unlock the workspace and begin the timer."}</p>
            </div>
            {CODE_REVIEW_TYPES.includes(task.task_type) ? (
              <div className="h-[440px]"><DiffViewer readOnly={!canEditWork} /></div>
            ) : (
              <div className="h-[440px]">
                <CodeEditor
                  key={`${project?.id || simulation?.project_id || "noproject"}-${task.task_id}`}
                  task={task}
                  project={project}
                  onCodeChange={setEditorCode}
                  onWorkChange={setHasEditorWork}
                  readOnly={!canEditWork}
                />
              </div>
            )}
          </section>
        )}

        {(task.task_metadata?.hints || task.hints) && (task.task_metadata?.hints || task.hints).length > 0 && (
          <section className="rounded-2xl border border-brand-500/30 bg-brand-500/5 p-4">
            <h3 className="mb-2 flex items-center gap-2 font-display text-xs font-bold uppercase tracking-[0.14em] text-brand-300">
              <span className="flex h-5 w-5 items-center justify-center rounded-md bg-brand-500/20 text-[11px]">💡</span>
              Contextual Task Hints
            </h3>
            <ul className="space-y-1.5 text-sm text-text-muted">
              {(task.task_metadata?.hints || task.hints).map((hint, idx) => (
                <li key={idx} className="flex items-start gap-2">
                  <span className="text-brand-400">•</span>
                  <span>{hint}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {task.task_metadata?.recommended_team_role && (
          <section className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3 text-sm">
            <span className="font-semibold text-text-faint">Team Member Available for Help: </span>
            <span className="text-brand-300 font-medium">{task.task_metadata.recommended_team_role}</span>
          </section>
        )}

        {task.task_metadata?.generation_reason && (
          <section className="rounded-xl border border-white/[0.06] bg-ink/30 p-3 text-xs text-text-faint">
            <span className="font-semibold">Dynamic Journey Note: </span>
            <span>{task.task_metadata.generation_reason}</span>
          </section>
        )}

        {scenario?.required_skills && scenario.required_skills.length > 0 && (
          <section>
            <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-text-faint mb-3">
              Required Skills
            </h3>
            <div className="flex flex-wrap gap-1">
              {scenario.required_skills.map((skill, i) => (
                <span
                  key={i}
                  className="px-2 py-1 rounded bg-status-todo/10 text-status-todo text-sm border border-status-todo/20"
                >
                  {skill}
                </span>
              ))}
            </div>
          </section>
        )}

        <section>
          <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-text-faint mb-3">
            Task Details
          </h3>
          <dl className="grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-text-faint">Task ID</dt>
            <dd className="font-mono text-xs">{task.task_id?.slice(0, 8)}...</dd>
            <dt className="text-text-faint">Simulation</dt>
            <dd className="font-mono text-xs">{simulation?.title}</dd>
            <dt className="text-text-faint">Scenario</dt>
            <dd>{scenario?.title}</dd>
            <dt className="text-text-faint">Difficulty</dt>
            <dd className="capitalize">{task.difficulty}</dd>
            <dt className="text-text-faint">Type</dt>
            <dd>{task.task_type?.replace("_", " ")}</dd>
          </dl>
        </section>

      </div>

      <div className="simulation-task-actions sticky bottom-0 z-10 space-y-3 border-t p-4">
        {canStart && (
          <motion.button
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            onClick={() => onStart(task.task_id)}
            disabled={isStarting}
            className="btn-primary w-full py-3 text-sm flex items-center justify-center gap-2"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9 9 4.03 9 9z" />
            </svg>
            {isStarting ? "Starting..." : "Start Task"}
          </motion.button>
        )}

        {canSubmit && (
          <motion.button
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            onClick={() => setShowSubmissionModal(true)}
            disabled={isSubmitting}
            className="btn-primary w-full py-3 text-sm flex items-center justify-center gap-2"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            {isSubmitting ? "Submitting..." : "Submit Work"}
          </motion.button>
        )}

        {task.status === "submitted" && (
          <div className="p-3 bg-status-review/10 border border-status-review/20 rounded text-center">
            <p className="text-sm text-status-review">
              Submitted for review. Waiting for evaluation...
            </p>
          </div>
        )}

        {task.status === "changes_requested" && (
          <motion.button
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            onClick={() => setShowSubmissionModal(true)}
            disabled={isSubmitting}
            className="btn-primary w-full py-2.5 text-sm"
          >
            ↻ Resubmit Task
          </motion.button>
        )}

        {task.status === "under_review" && (
          <div className="p-3 bg-status-review/10 border border-status-review/20 rounded text-center">
            {task.review_status === "failed" ? (
              <>
                <p className="text-sm text-status-blocked" role="alert">
                  {task.review_error || "Technical review failed. Your submission is saved; no score was assigned."}
                </p>
                {onRetryReview && task.review_version_id && (
                  <button className="btn-ghost mt-2" onClick={() => onRetryReview(task.review_version_id)}>
                    Retry technical review
                  </button>
                )}
              </>
            ) : (
              <p className="text-sm text-status-review">AI review in progress...</p>
            )}
          </div>
        )}

        {task.status === "manager_approval" && (
          <div className="rounded-2xl border border-status-completed/25 bg-status-completed/[0.06] p-4 text-center">
            <p className="text-sm font-bold text-status-completed">✓ Approved — ready to complete</p>
            <p className="mt-1 text-xs leading-relaxed text-text-muted">
              The review approved this work. Complete the task to lock in progress and unlock the next one.
            </p>
            {onComplete && (
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.98 }}
                onClick={() => onComplete(task.task_id)}
                disabled={isCompleting}
                className="btn-secondary mt-3 w-full py-2.5 text-sm"
              >
                {isCompleting ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="h-3.5 w-3.5 rounded-full border-2 border-current/30 border-t-current animate-spin" style={{ animationDuration: "0.7s" }} />
                    Completing…
                  </span>
                ) : (
                  "✓ Complete Task"
                )}
              </motion.button>
            )}
          </div>
        )}

        {(task.status === "changes_requested" || task.review_feedback) && (
          <ReviewFeedbackPanel feedback={task.review_feedback} />
        )}

        {task.status === "completed" && (
          <div className="p-3 bg-status-completed/10 border border-status-completed/20 rounded text-center">
            <p className="text-sm text-status-completed font-medium">
              Task Completed!
            </p>
            <p className="text-xs text-text-muted mt-1">
              Great work! Progressing to next task...
            </p>
          </div>
        )}

        {deadlineMissed && (
          <div className="deadline-late-note" role="status">
            <span className="deadline-late-icon">!</span>
            <p><strong>Deadline missed.</strong> You can still submit your work; it will be marked as late.</p>
          </div>
        )}
      </div>

      {showSubmissionModal && (
        <SubmissionModal
          task={task}
          onClose={() => setShowSubmissionModal(false)}
          onSubmit={handleSubmitWithCode}
          isSubmitting={isSubmitting}
          hasEditorWork={hasEditorWork}
        />
      )}
    </div>
  );
}

// Module scope (NOT inside TaskPanel): defining it inside the component body
// creates a new component type on every render, which unmounts/remounts the
// whole section — constant flicker on the 1s countdown timer and 5s polling.
function ReviewFeedbackPanel({ feedback }) {
  if (!feedback) {
    return (
      <div className="rounded-xl border border-status-progress/25 bg-status-progress/[0.06] p-4">
        <p className="text-sm font-bold text-status-progress">Changes requested</p>
        <p className="mt-1 text-xs leading-relaxed text-text-muted">
          Your submission needs revisions. Review the feedback and resubmit.
        </p>
      </div>
    );
  }
  const required = feedback.required_changes || [];
  const unmet = feedback.unmet_criteria || [];
  const testItems = feedback.test_criteria || [];
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="overflow-hidden rounded-xl border border-status-progress/25 bg-status-progress/[0.04]"
    >
      <div className="border-b border-status-progress/20 bg-status-progress/[0.07] px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-bold text-status-progress">
            {feedback.is_previous_review ? "Previous AI feedback" : "Changes requested"}
          </p>
          {feedback.score != null && (
            <span className="chip font-mono text-[11px]!">
              AI score <strong className="text-status-progress">{feedback.score}/100</strong>
            </span>
          )}
        </div>
          {feedback.summary && (
            <p className="mt-1.5 text-[13px] leading-relaxed text-text">{feedback.summary}</p>
          )}
        </div>

        {feedback.missing_evidence && (
        <div className="border-b border-status-progress/25 bg-status-progress/[0.08] px-4 py-2.5">
            <p className="text-xs font-semibold leading-relaxed text-status-progress">
              ⚠ The reviewer found no analyzable implementation — nothing below is inferred from code.
              Provide the evidence listed under “What to change” and resubmit.
            </p>
        </div>
      )}

      <div className="space-y-4 p-4">
        {unmet.length > 0 && (
          <section>
            <h4 className="mb-2 text-[11px] font-bold uppercase tracking-[0.12em] text-status-progress">
              ⚠ What was wrong — requirements not met
            </h4>
            <ul className="space-y-2">
              {unmet.map((item, i) => (
                <li key={i} className="rounded-lg border border-status-progress/20 bg-white px-3.5 py-2.5">
                  <p className="text-[13px] font-semibold leading-snug text-text">{item.criterion}</p>
                  {item.evidence && (
                    <p className="mt-1 text-xs leading-relaxed text-text-muted">
                      <span className="font-semibold text-text-faint">Evidence: </span>{item.evidence}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

      {feedback.adaptive_decision?.status === "applied" && (
        <div className="border-b border-accent-cyan/20 bg-accent-cyan/[0.05] px-4 py-3">
          <p className="text-xs font-bold uppercase tracking-wider text-accent-cyan">Technical difficulty adaptation</p>
          <p className="mt-1 text-sm font-semibold capitalize">{feedback.adaptive_decision.previous_difficulty} → {feedback.adaptive_decision.next_difficulty}</p>
          <p className="mt-1 text-xs leading-relaxed text-text-muted">{feedback.adaptive_decision.reason}</p>
          <p className="mt-1 text-[11px] text-text-faint">{feedback.adaptive_decision.evaluation_source === "gemini" ? "AI evaluation" : `Fallback evaluation (${feedback.adaptive_decision.evaluation_source || "mock"})`}</p>
        </div>
      )}

        {required.length > 0 && (
          <section>
            <h4 className="mb-2 text-[11px] font-bold uppercase tracking-[0.12em] text-status-progress">
              🔧 What to change — exact fixes
            </h4>
            <ol className="space-y-2">
              {required.map((change, i) => (
                <motion.li
                  key={i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: Math.min(i * 0.06, 0.3) }}
                  className="flex items-start gap-2.5 rounded-lg border border-border bg-white px-3.5 py-2.5"
                >
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-status-progress/10 font-mono text-[11px] font-bold text-status-progress">
                    {i + 1}
                  </span>
                  <span className="text-[13px] leading-relaxed text-text">{change}</span>
                </motion.li>
              ))}
            </ol>
          </section>
        )}

        {(feedback.testing_feedback || testItems.length > 0) && (
          <section>
            <h4 className="mb-2 text-[11px] font-bold uppercase tracking-[0.12em] text-status-progress">
              🧪 What to test before resubmitting
            </h4>
            <div className="rounded-xl border border-white/[0.07] bg-white/[0.03] px-3.5 py-2.5 text-[13px] leading-relaxed text-text-muted">
              {feedback.testing_feedback && <p>{feedback.testing_feedback}</p>}
              {testItems.length > 0 && (
                <ul className="mt-1.5 space-y-1">
                  {testItems.map((criterion, i) => (
                    <li key={i} className="flex gap-1.5">
                      <span className="text-status-progress">▸</span><span>{criterion}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        )}

        {required.length > 0 && (
          <section>
            <h4 className="mb-2 text-[11px] font-bold uppercase tracking-[0.12em] text-status-progress">
              ✅ Resubmission checklist
            </h4>
            <ul className="space-y-1.5">
              {required.map((change, i) => (
                <li key={i} className="flex items-start gap-2 text-xs leading-relaxed text-text-muted">
                  <span className="mt-0.5 inline-block h-3.5 w-3.5 shrink-0 rounded border border-status-progress/50" />
                  <span className="line-clamp-2">{change}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {required.length === 0 && unmet.length === 0 && (
          <p className="text-xs leading-relaxed text-text-muted">
            Review the AI summary above, revise your work, then resubmit from this panel.
          </p>
        )}
      </div>
    </motion.div>
  );
}
