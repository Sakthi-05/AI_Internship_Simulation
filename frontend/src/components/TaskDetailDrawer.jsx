import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { criteriaLines, criteriaStateLabel, earlierCriteria } from "./simulation/criteriaDisplay";
import { STATUS_META, PRIORITY_META } from "../lib/status";
import { useReportBug } from "../hooks/useApi";
import { useSimulation } from "../context/SimulationContext";

export default function TaskDetailDrawer({ task, allTasks, onClose, onUpdateStatus, isUpdating }) {
  const { simulation } = useSimulation();
  const [deliverableUrl, setDeliverableUrl] = useState("");
  const [blockedReason, setBlockedReason] = useState("");
  const [bugDescription, setBugDescription] = useState("");
  const reportBug = useReportBug(simulation?.projectId);

  if (!task) return null;

  const isLocked = task.locked === true;

  const dependencies = (task.depends_on_task_ids || [])
    .map((id) => allTasks.find((t) => t.id === id))
    .filter(Boolean);

  const dependenciesIncomplete = dependencies.some((d) => d.status !== "completed");

  const act = (status, extra = {}) => onUpdateStatus({ taskId: task.id, status, ...extra });

  return (
    <AnimatePresence>
      <motion.div
        className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      />
      <motion.div
        className="fixed right-0 top-0 z-50 h-full w-full max-w-md overflow-y-auto border-l border-white/10 bg-surface/95 p-5 shadow-[-40px_0_100px_-20px_rgba(0,0,0,0.9)] backdrop-blur-2xl"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 30, stiffness: 300 }}
      >
        <div className="pointer-events-none absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-brand-500/10 to-transparent" />
        <div className="relative flex items-start justify-between gap-3">
          <div>
            <span
              className="rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide"
              style={{
                color: STATUS_META[task.status].color,
                borderColor: `color-mix(in srgb, ${STATUS_META[task.status].color} 40%, transparent)`,
                backgroundColor: `color-mix(in srgb, ${STATUS_META[task.status].color} 12%, transparent)`,
                boxShadow: `0 0 16px -6px ${STATUS_META[task.status].color}`,
              }}
            >
              ● {STATUS_META[task.status].label}
            </span>
            <h2 className="mt-2.5 font-display text-lg font-bold leading-snug">{task.title}</h2>
          </div>
          <button onClick={onClose} className="btn-ghost shrink-0">✕ Close</button>
        </div>

        <p className="mt-4 text-sm leading-relaxed text-text-muted">{task.description}</p>

        {(task.deliverables || task.task_metadata?.deliverables || []).length > 0 && (
          <Section title="Expected deliverables">
            <ul className="list-inside list-disc space-y-1 text-sm text-text-muted">
              {(task.deliverables || task.task_metadata?.deliverables || []).map((item, index) => (
                <li key={`${index}-${item}`}>{typeof item === "string" ? item : item?.name || item?.description || JSON.stringify(item)}</li>
              ))}
            </ul>
          </Section>
        )}

        <Section title={task.criteria_version?.active_version_number ? `Acceptance criteria · version ${task.criteria_version.active_version_number}` : "Acceptance criteria"}>
          <ul className="list-inside list-disc space-y-1 text-sm text-text-muted">
            {criteriaLines(task).map((criterion) => (
              <li key={criterion.text}>
                {criterion.text}
                {task.criteria_version ? <span className="ml-2 text-xs uppercase tracking-wide text-text-faint">{criteriaStateLabel(criterion.state)}</span> : null}
              </li>
            ))}
          </ul>
          {earlierCriteria(task).map((version) => (
            <div key={version.versionNumber} className="mt-3 text-sm text-text-muted">
              <p className="text-xs font-bold uppercase tracking-wide text-text-faint">Version {version.versionNumber} · Superseded</p>
              <ul className="list-inside list-disc">
                {version.items.map((text) => <li key={text}>{text}</li>)}
              </ul>
            </div>
          ))}
        </Section>

        <Section title="Details">
          <dl className="grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-text-faint">Sprint</dt>
            <dd>{task.sprint_name || (task.sprint_number ? `Sprint ${task.sprint_number}` : "—")}</dd>
            <dt className="text-text-faint">Priority</dt>
            <dd style={{ color: PRIORITY_META[task.priority].color }}>{PRIORITY_META[task.priority].label}</dd>
            <dt className="text-text-faint">Estimated hours</dt>
            <dd>{task.estimated_hours}h</dd>
            <dt className="text-text-faint">Deadline</dt>
            <dd>{task.deadline ? new Date(task.deadline).toLocaleDateString() : "—"}</dd>
          </dl>
        </Section>

        {dependencies.length > 0 && (
          <Section title="Dependencies">
            <ul className="space-y-1 text-sm">
              {dependencies.map((d) => (
                <li key={d.id} className="flex items-center gap-2">
                  <span style={{ color: STATUS_META[d.status].color }}>●</span>
                  <span className={d.status === "completed" ? "text-text-muted" : "text-text"}>{d.title}</span>
                </li>
              ))}
            </ul>
            {dependenciesIncomplete && task.status === "backlog" && (
              <p className="mt-2 text-xs text-status-blocked">
                Cannot start until all dependencies are completed.
              </p>
            )}
          </Section>
        )}

        <Section title="Actions">
          {isLocked && (
            <p className="mb-3 text-xs text-text-faint flex items-center gap-1.5">
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
              This task is locked. Complete the current task first.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <ActionButton
              disabled={isLocked || isUpdating || task.status === "in_progress" || task.status === "completed" || dependenciesIncomplete}
              onClick={() => act("in_progress")}
            >
              Start
            </ActionButton>
            <ActionButton
              disabled={isLocked || isUpdating || task.status !== "in_progress"}
              onClick={() => act("todo")}
            >
              Pause
            </ActionButton>
            <ActionButton
              disabled={isLocked || isUpdating || task.status !== "in_progress"}
              onClick={() => act("submitted")}
            >
              Submit for review
            </ActionButton>
            <ActionButton
              disabled={isLocked || isUpdating || !["submitted", "under_review", "manager_approval"].includes(task.status)}
              onClick={() => act("completed", deliverableUrl ? { deliverable_url: deliverableUrl } : {})}
              accent="var(--color-status-completed)"
            >
              Mark complete
            </ActionButton>
          </div>

          <div className="mt-3 space-y-2">
            <input
              className="input"
              placeholder="Deliverable link (repo, PR, doc...)"
              value={deliverableUrl}
              onChange={(e) => setDeliverableUrl(e.target.value)}
            />
            {task.deliverable_url && (
              <a
                href={task.deliverable_url}
                target="_blank"
                rel="noreferrer"
                className="block truncate text-xs text-status-todo underline"
              >
                {task.deliverable_url}
              </a>
            )}
          </div>

          <div className="mt-3 flex gap-2">
            <input
              className="input"
              placeholder="Why is this blocked?"
              value={blockedReason}
              onChange={(e) => setBlockedReason(e.target.value)}
            />
            <button
              className="btn-ghost shrink-0"
              disabled={isUpdating || !blockedReason || task.status !== "in_progress"}
              onClick={() => act("blocked", { blocked_reason: blockedReason })}
            >
              Mark blocked
            </button>
          </div>
          {task.blocked_reason && task.status === "blocked" && (
            <p className="mt-2 text-xs text-status-blocked">Blocked: {task.blocked_reason}</p>
          )}

          <div className="mt-4 flex gap-2 border-t border-border-soft pt-3">
            <input
              className="input"
              placeholder="Report a bug on this task"
              value={bugDescription}
              onChange={(e) => setBugDescription(e.target.value)}
            />
            <button
              className="btn-ghost shrink-0"
              style={{ borderColor: "var(--color-status-blocked)", color: "var(--color-status-blocked)" }}
              disabled={reportBug.isPending || !bugDescription}
              onClick={async () => {
                await reportBug.mutateAsync({ taskId: task.id, description: bugDescription });
                setBugDescription("");
              }}
            >
              Report bug
            </button>
          </div>
          {reportBug.error && <p className="mt-2 text-xs text-status-blocked" role="alert">Bug report could not be saved: {reportBug.error.message}</p>}
        </Section>
      </motion.div>
    </AnimatePresence>
  );
}

function Section({ title, children }) {
  return (
    <div className="mt-5 border-t border-border-soft pt-4">
      <h3 className="mb-2 font-display text-xs font-semibold uppercase tracking-wide text-text-faint">
        {title}
      </h3>
      {children}
    </div>
  );
}

function ActionButton({ children, disabled, onClick, accent }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="btn-ghost disabled:cursor-not-allowed disabled:opacity-40"
      style={accent && !disabled ? { borderColor: accent, color: accent } : undefined}
    >
      {children}
    </button>
  );
}
