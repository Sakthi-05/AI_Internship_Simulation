import { motion } from "framer-motion";

export default function ProgressIndicator({ simulation, progress, scenario, currentTask }) {
  if (!simulation || !progress) return null;
  const overallPct = Math.min(100, Math.max(0, Math.round(Number(progress.overall_progress) || 0)));
  const currentDeadlineWarning = !currentTask?.deadline_missed && Boolean(
    currentTask?.remaining_time && currentTask.remaining_time.hours === 0 && currentTask.remaining_time.minutes < 5
  );

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      className="simulation-progress"
    >
      {/* Main Progress Bar */}
      <div className="px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-[180px] flex-1">
            <div className="mb-2 flex items-baseline justify-between gap-3">
              <span className="text-xs font-semibold text-text-muted">Simulation progress</span>
              <motion.span
                key={overallPct}
                initial={{ opacity: 0.7 }}
                animate={{ opacity: 1, scale: 1 }}
                className="font-display text-lg font-bold tabular-nums text-status-completed"
              >
                {overallPct}<span className="ml-0.5 text-xs font-semibold">%</span>
              </motion.span>
            </div>
            <div className="progress-track h-2.5">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${overallPct}%` }}
                className="progress-fill"
                transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
              />
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <div className="progress-count progress-count-done">
              <p className="text-[9px] font-bold uppercase tracking-wider text-text-faint">Completed</p>
              <p className="font-mono text-base font-bold text-status-completed">
                {progress.completed_tasks}<span className="text-xs text-text-faint">/{progress.total_tasks}</span>
              </p>
            </div>
            <div className="progress-count">
              <p className="text-[9px] font-bold uppercase tracking-wider text-text-faint">Remaining</p>
              <p className="font-mono text-base font-bold text-text-muted">
                {progress.remaining_tasks}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Scenario Progress Tabs */}
      {progress.scenarios && progress.scenarios.length > 0 && (
        <div className="scenario-progress-row">
          <div className="flex items-center gap-2 overflow-x-auto pb-1">
            {progress.scenarios.map((sc) => (
              <ScenarioProgressTab
                key={sc.scenario_id}
                scenario={sc}
                isActive={scenario?.id === sc.scenario_id}
                isCompleted={sc.status === "completed"}
              />
            ))}
          </div>
        </div>
      )}

      {/* Current Task Indicator */}
      {currentTask && (
        <div className="current-task-strip">
          <div className="flex items-center gap-3">
            <div className="h-2 w-2 shrink-0 rounded-full bg-brand-500" />
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-text-faint">Now working on</p>
              <p className="truncate text-sm font-semibold">{currentTask.title}</p>
            </div>
            {currentTask.remaining_time && (
              <div className={`shrink-0 rounded-lg border px-2.5 py-1.5 text-right ${currentTask.deadline_missed ? "border-status-blocked/25 bg-status-blocked/5" : currentDeadlineWarning ? "border-status-progress/25 bg-status-progress/5" : "border-border bg-white"}`}>
                <p className="text-[9px] font-semibold uppercase tracking-wider text-text-faint">{currentTask.deadline_missed ? "Deadline" : currentDeadlineWarning ? "Due soon" : "Time left"}</p>
                <p className={`font-mono text-xs font-bold tabular-nums ${currentTask.deadline_missed ? "text-status-blocked" : currentDeadlineWarning ? "text-status-progress" : "text-text"}`}>
                  {currentTask.deadline_missed ? "Expired" : currentTask.remaining_time.formatted.replace(/^Warning:\s*/, "")}
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </motion.div>
  );
}

function ScenarioProgressTab({ scenario, isActive, isCompleted }) {
  const progressPct = scenario.total_tasks > 0
    ? Math.min(100, Math.max(0, Math.round((scenario.completed_tasks / scenario.total_tasks) * 100)))
    : 0;

  return (
    <motion.button
      whileHover={{ scale: 1.03, y: -1 }}
      whileTap={{ scale: 0.98 }}
      className={`scenario-progress-tab flex-shrink-0 rounded-lg border px-3 py-2 text-left transition-colors ${
        isActive
          ? "scenario-progress-active border-brand-500/25 bg-brand-500/5 text-text"
          : isCompleted
            ? "scenario-progress-complete border-status-completed/20 bg-status-completed/5 text-status-completed"
            : "border-border bg-white text-text-muted hover:border-brand-300"
      }`}
      style={{ minWidth: "140px" }}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold">
          {scenario.title.length > 20 ? scenario.title.slice(0, 20) + "..." : scenario.title}
        </span>
        {isCompleted ? (
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-status-completed text-[10px] text-white">✓</span>
        ) : isActive ? (
          <span className="h-2 w-2 shrink-0 rounded-full bg-brand-500" />
        ) : null}
      </div>
      <div className="progress-track h-1.5">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${progressPct}%` }}
          className={isCompleted ? "h-full rounded-full bg-status-completed" : "progress-fill"}
          transition={{ duration: 0.6 }}
        />
      </div>
      <p className="mt-1 text-[10px] font-medium">
        {scenario.completed_tasks} / {scenario.total_tasks} tasks
      </p>
    </motion.button>
  );
}
