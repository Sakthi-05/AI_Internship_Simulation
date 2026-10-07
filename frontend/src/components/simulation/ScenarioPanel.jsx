import { motion } from "framer-motion";

export default function ScenarioPanel({ scenario, simulation, progress, company }) {
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
    <div className="card flex flex-col overflow-hidden">
      {/* Scenario Header */}
      <div className="relative border-b border-white/[0.06] bg-white/[0.02] p-4">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-16 bg-gradient-to-b from-brand-500/10 to-transparent" />
        <div className="relative flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex items-center gap-1.5">
              <span className="chip chip-glow font-mono text-[10px]!">
                ◈ Scenario {scenario.sequence}
              </span>
              <span className="chip text-[10px]! capitalize">
                {scenario.difficulty}
              </span>
            </div>
            <h2 className="font-display text-lg font-bold leading-snug">{scenario.title}</h2>
            <p className="mt-1 text-sm leading-relaxed text-text-muted">{scenario.description}</p>
          </div>
          <div className="shrink-0 rounded-xl border border-white/[0.07] bg-ink/50 px-3 py-2 text-center">
            <p className="gradient-text font-mono text-xl font-bold">{scenarioProgressPct}%</p>
            <p className="font-mono text-[10px] text-text-muted">{completedTasks}/{totalTasks}</p>
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
      <div className="p-4 border-b border-border bg-surface-2/50">
        <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-text-faint mb-2">
          Workplace Context
        </h3>
        <div className="prose prose-sm text-text-muted">
          {scenario.workplace_context.split('\n').map((paragraph, i) => (
            <p key={i} className="mb-2">{paragraph}</p>
          ))}
        </div>
      </div>

      {/* Objectives */}
      {scenario.objectives && scenario.objectives.length > 0 && (
        <div className="p-4 border-b border-border">
          <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-text-faint mb-2">
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
          <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-text-faint mb-2">
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
      <div className="flex-1 p-4 overflow-y-auto">
        <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-text-faint mb-3">
          Scenario Tasks
        </h3>
        <ul className="space-y-2">
          {scenario.all_tasks?.map((task) => (
            <ScenarioTaskItem
              key={task.task_id}
              task={task}
              isCurrent={simulation.current_task_id === task.task_id}
              locked={task.locked}
            />
          ))}
        </ul>
      </div>
    </div>
  );
}

function ScenarioTaskItem({ task, isCurrent, locked }) {
  const statusColors = {
    backlog: "bg-surface-2 text-text-muted",
    todo: "bg-status-todo/10 text-status-todo",
    in_progress: "bg-status-todo/10 text-status-todo",
    submitted: "bg-status-review/10 text-status-review",
    under_review: "bg-status-review/10 text-status-review",
    completed: "bg-status-completed/10 text-status-completed",
    changes_requested: "bg-status-blocked/10 text-status-blocked",
    blocked: "bg-status-blocked/10 text-status-blocked",
  };

  return (
    <motion.li
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      whileHover={locked ? undefined : { x: 3 }}
      className={`rounded-xl border p-3 transition-all duration-200 ${
        locked
          ? "border-white/[0.04] bg-white/[0.01] opacity-55"
          : isCurrent
            ? "border-brand-400/50 bg-gradient-to-r from-brand-500/[0.14] to-accent-cyan/[0.06] shadow-[0_0_28px_-10px_rgba(124,108,246,0.8)]"
            : "border-white/[0.06] bg-white/[0.02] hover:border-brand-400/25"
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
          <div className="flex items-center gap-2">
            <span className={`font-medium text-sm truncate ${locked ? "text-text-muted" : ""}`}>{task.title}</span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wide ${statusColors[task.status]}`}
            >
              {task.status.replace("_", " ")}
            </span>
            {locked && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wide bg-surface-2 text-text-faint">
                locked
              </span>
            )}
          </div>
          <p className={`mt-1 text-xs truncate ${locked ? "text-text-faint" : "text-text-muted"}`}>{task.description}</p>
          <div className="mt-1 flex items-center gap-3 text-xs text-text-faint">
            {task.task_type && <span>{task.task_type}</span>}
            {task.deadline && (
              <span>Due: {new Date(task.deadline).toLocaleDateString()}</span>
            )}
          </div>
        </div>
      </div>
    </motion.li>
  );
}
