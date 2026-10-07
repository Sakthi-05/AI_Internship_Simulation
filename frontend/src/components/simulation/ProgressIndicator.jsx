import { motion } from "framer-motion";

export default function ProgressIndicator({ simulation, progress, scenario, currentTask }) {
  if (!simulation || !progress) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative bg-ink/40 backdrop-blur-xl"
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-brand-400/70 to-transparent" />
      {/* Main Progress Bar */}
      <div className="px-4 py-3">
        <div className="mb-2 flex items-center justify-between gap-4">
          <div className="flex-1">
            <div className="mb-1.5 flex items-center gap-2">
              <span className="eyebrow">
                Overall Progress
              </span>
              <motion.span
                key={progress.overall_progress}
                initial={{ scale: 1.25 }}
                animate={{ scale: 1 }}
                className="gradient-text font-mono text-lg font-bold"
              >
                {progress.overall_progress}%
              </motion.span>
            </div>
            <div className="progress-track h-3">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${progress.overall_progress}%` }}
                className="progress-fill"
                transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
              />
            </div>
          </div>

          <div className="hidden shrink-0 items-center gap-3 sm:flex">
            <div className="rounded-xl border border-status-completed/25 bg-status-completed/[0.07] px-3.5 py-2 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-text-faint">Done</p>
              <p className="font-mono text-lg font-bold text-status-completed">
                {progress.completed_tasks}<span className="text-xs text-text-faint">/{progress.total_tasks}</span>
              </p>
            </div>
            <div className="rounded-xl border border-white/[0.07] bg-white/[0.03] px-3.5 py-2 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-text-faint">Left</p>
              <p className="font-mono text-lg font-bold text-text-muted">
                {progress.remaining_tasks}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Scenario Progress Tabs */}
      {progress.scenarios && progress.scenarios.length > 0 && (
        <div className="border-t border-white/[0.05] px-4 pb-3 pt-2.5">
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
        <div className="border-t border-white/[0.05] bg-gradient-to-r from-brand-500/[0.08] to-transparent px-4 py-2.5">
          <div className="flex items-center gap-3">
            <div className="h-2 w-2 shrink-0 rounded-full bg-accent-cyan shadow-[0_0_10px_2px_rgba(70,216,255,0.7)] animate-pulse-soft" />
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-text-faint">Now working on</p>
              <p className="truncate text-sm font-semibold">{currentTask.title}</p>
            </div>
            {currentTask.remaining_time && (
              <div className={`shrink-0 rounded-xl border px-3 py-1.5 text-right ${currentTask.deadline_missed ? "border-status-blocked/40 bg-status-blocked/10" : "border-brand-400/30 bg-brand-500/10"}`}>
                <p className="text-[10px] uppercase tracking-wider text-text-faint">Time Left</p>
                <p className={`font-mono text-sm font-bold tabular-nums ${currentTask.deadline_missed ? "text-status-blocked" : "text-brand-200"}`}>
                  {currentTask.remaining_time.formatted}
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
    ? Math.round((scenario.completed_tasks / scenario.total_tasks) * 100) 
    : 0;

  return (
    <motion.button
      whileHover={{ scale: 1.03, y: -1 }}
      whileTap={{ scale: 0.98 }}
      className={`flex-shrink-0 rounded-xl border px-3 py-2 text-left transition-all duration-200 ${
        isActive
          ? "border-brand-400/50 bg-gradient-to-br from-brand-500/20 to-accent-cyan/10 text-text shadow-[0_0_24px_-8px_rgba(124,108,246,0.8)]"
          : isCompleted
            ? "border-status-completed/30 bg-status-completed/[0.06] text-status-completed"
            : "border-white/[0.06] bg-white/[0.02] text-text-muted hover:border-white/15"
      }`}
      style={{ minWidth: "140px" }}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold">
          {scenario.title.length > 20 ? scenario.title.slice(0, 20) + "..." : scenario.title}
        </span>
        {isCompleted ? (
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-status-completed text-[10px] text-ink shadow-[0_0_10px_rgba(62,214,149,0.8)]">✓</span>
        ) : isActive ? (
          <span className="h-2 w-2 shrink-0 rounded-full bg-accent-cyan shadow-[0_0_10px_2px_rgba(70,216,255,0.8)] animate-pulse-soft" />
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
      <p className="mt-1 font-mono text-[11px]">
        {scenario.completed_tasks} / {scenario.total_tasks} tasks
      </p>
    </motion.button>
  );
}