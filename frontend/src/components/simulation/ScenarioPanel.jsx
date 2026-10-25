import { motion } from "framer-motion";

export default function ScenarioPanel({ scenario, simulation, progress }) {
  if (!scenario) {
    return (
      <div className="card p-4 flex flex-col">
        <div className="text-center text-text-muted py-8">
          <p>No active scenario</p>
        </div>
      </div>
    );
  }

  const scenarioProgress = progress?.scenarios?.find(s => s.scenario_id === scenario.id);
  const completedTasks = scenarioProgress?.completed_tasks || 0;
  const totalTasks = scenarioProgress?.total_tasks || 0;
  const scenarioProgressPct = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0;

  return (
    <div className="simulation-scenario-panel card flex min-h-0 flex-col overflow-hidden">
      {/* Scenario Header */}
      <div className="scenario-panel-header relative border-b p-4">
        <div className="relative flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex items-center gap-1.5">
              <span className="chip chip-glow text-[10px]!">
                Scenario {scenario.sequence}
              </span>
              <span className="chip text-[10px]! capitalize">
                {scenario.difficulty}
              </span>
            </div>
            <h2 className="font-display text-base font-bold leading-snug">{scenario.title}</h2>
            <p className="mt-1.5 text-xs leading-5 text-text-muted">{scenario.description}</p>
          </div>
          <div className="scenario-progress-summary shrink-0 rounded-lg border px-3 py-2 text-center">
            <p className="font-display text-lg font-bold text-status-completed">{scenarioProgressPct}<span className="text-[10px]">%</span></p>
            <p className="text-[10px] text-text-muted">{completedTasks}/{totalTasks}</p>
          </div>
        </div>

        {/* Progress bar */}
        <div className="progress-track relative mt-3 h-2">
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: `${scenarioProgressPct}%` }}
            className="progress-fill"
            transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
          />
        </div>
      </div>

      {/* Workplace Context */}
      <div className="workplace-context-panel border-b p-4">
        <h3 className="font-display text-[10px] font-bold uppercase tracking-[.1em] text-text-faint mb-2">
          Workplace Context
        </h3>
        <div className="space-y-2 text-xs leading-5 text-text-muted">
          {(scenario.workplace_context || "Workplace context has not been provided for this scenario.").split('\n').filter(Boolean).map((paragraph, i) => (
            <p key={i}>{paragraph}</p>
          ))}
        </div>
      </div>

      {/* Objectives */}
      {scenario.objectives && scenario.objectives.length > 0 && (
        <div className="p-4 border-b border-border">
          <h3 className="font-display text-[10px] font-bold uppercase tracking-[.1em] text-text-faint mb-2">
            Objectives
          </h3>
          <ul className="space-y-1">
            {scenario.objectives.map((obj, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-text-muted">
                <span className="w-1.5 h-1.5 mt-2 rounded-full bg-status-todo shrink-0" />
                {obj}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Required Skills */}
      {scenario.required_skills && scenario.required_skills.length > 0 && (
        <div className="p-4 border-b border-border">
          <h3 className="font-display text-[10px] font-bold uppercase tracking-[.1em] text-text-faint mb-2">
            Required Skills
          </h3>
          <div className="flex flex-wrap gap-1">
            {scenario.required_skills.map((skill, i) => (
              <span
                key={i}
                className="px-2 py-0.5 rounded text-xs bg-surface-2 text-text-muted border border-border"
              >
                {skill}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Task List for this Scenario */}
      <div className="scenario-task-list min-h-0 min-w-0 flex-1 p-4 overflow-y-auto">
        <div className="mb-3 flex items-center justify-between"><h3 className="font-display text-[10px] font-bold uppercase tracking-[.1em] text-text-faint">Scenario tasks</h3><span className="text-[10px] text-text-faint">{scenario.all_tasks?.length ?? "—"} total</span></div>
        <ul className="space-y-2">
          {scenario.all_tasks?.map((task) => (
            <ScenarioTaskItem
              key={task.task_id}
              task={task}
              isCurrent={simulation.current_task_id === task.task_id}
              locked={task.locked}
            />
          ))}
          {(!scenario.all_tasks || scenario.all_tasks.length === 0) && <li className="rounded-lg border border-dashed border-border bg-surface-3 px-3 py-4 text-center text-xs text-text-muted">No scenario tasks are available.</li>}
        </ul>
      </div>
    </div>
  );
}

function ScenarioTaskItem({ task, isCurrent, locked }) {
  const statusColors = {
    backlog: "bg-surface-3 text-text-muted",
    todo: "bg-status-todo/10 text-status-todo",
    in_progress: "bg-status-todo/10 text-status-todo",
    submitted: "bg-status-review/10 text-status-review",
    under_review: "bg-status-review/10 text-status-review",
    completed: "bg-status-completed/10 text-status-completed",
    changes_requested: "bg-status-progress/10 text-status-progress",
    blocked: "bg-status-progress/10 text-status-progress",
  };

  return (
    <motion.li
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      whileHover={locked ? undefined : { x: 3 }}
      className={`scenario-task rounded-lg border p-3 transition-colors ${
        locked
          ? "scenario-task-locked opacity-60"
          : isCurrent
            ? "scenario-task-current border-brand-500/30 bg-brand-500/5"
            : "border-border bg-white hover:border-brand-300"
      }`}
    >
      <div className="flex items-start gap-2">
        {locked ? (
          <svg className="w-4 h-4 mt-1 text-text-faint shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
        ) : (
          <div className={`w-2 h-2 mt-2 rounded-full shrink-0 ${statusColors[task.status]?.replace("bg-", "bg-").replace("text-", "bg-") || "bg-surface-2"}`} />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <span className={`min-w-0 flex-1 truncate font-medium text-xs ${locked ? "text-text-muted" : ""}`}>{task.title}</span>
            <span
              className={`shrink-0 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide ${statusColors[task.status]}`}
            >
              {task.status.replace("_", " ")}
            </span>
            {locked && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wide bg-surface-2 text-text-faint">
                locked
              </span>
            )}
          </div>
          <p className={`mt-1 text-[11px] leading-4 ${locked ? "text-text-faint" : "text-text-muted"}`}>{task.description}</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-faint">
            {task.task_type && <span>{task.task_type.replaceAll("_", " ")}</span>}
            {task.deadline && (
              <span>Due: {new Date(task.deadline).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span>
            )}
          </div>
        </div>
      </div>
    </motion.li>
  );
}
