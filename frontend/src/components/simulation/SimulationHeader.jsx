import { motion } from "framer-motion";

export default function SimulationHeader({ simulation, progress }) {
  if (!simulation) return null;

  const statusColors = {
    not_started: "border-white/15 bg-white/[0.05] text-text-faint",
    in_progress: "border-brand-400/40 bg-brand-500/15 text-brand-200 shadow-[0_0_16px_-6px_rgba(124,108,246,0.8)]",
    completed: "border-status-completed/40 bg-status-completed/10 text-status-completed",
    abandoned: "border-status-blocked/40 bg-status-blocked/10 text-status-blocked",
  };

  const statusLabels = {
    not_started: "Not Started",
    in_progress: "In Progress",
    completed: "Completed",
    abandoned: "Abandoned",
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative flex items-center justify-between gap-4 overflow-hidden border-b border-white/[0.06] bg-ink/50 p-4 backdrop-blur-xl"
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-brand-400/60 to-transparent" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="truncate font-display text-lg font-bold">{simulation.title}</h1>
          <span
            className={`rounded-full border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ${statusColors[simulation.status] || "border-white/15 bg-white/[0.05] text-text-faint"}`}
          >
            ● {statusLabels[simulation.status] || simulation.status}
          </span>
          <span className="chip text-[11px]!">
            {simulation.role}
          </span>
          <span className="chip text-[11px]! capitalize">
            {simulation.difficulty}
          </span>
        </div>
        <p className="mt-1 truncate text-sm text-text-muted">{simulation.description}</p>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        {/* Timer/Progress */}
        <div className="hidden rounded-xl border border-brand-400/25 bg-brand-500/10 px-3.5 py-1.5 text-right sm:block">
          <p className="text-[10px] uppercase tracking-wider text-text-faint">Overall Progress</p>
          <p className="gradient-text font-mono text-xl font-bold">{progress?.overall_progress || 0}%</p>
        </div>

        {/* Time elapsed */}
        {simulation.started_at && (
          <div className="hidden rounded-xl border border-white/[0.07] bg-white/[0.03] px-3.5 py-1.5 text-right md:block">
            <p className="text-[10px] uppercase tracking-wider text-text-faint">Time Elapsed</p>
            <p className="font-mono text-lg font-semibold tabular-nums" id="elapsed-timer">
              {formatElapsedTime(simulation.started_at)}
            </p>
          </div>
        )}
      </div>
    </motion.div>
  );
}

function formatElapsedTime(startedAt) {
  const start = new Date(startedAt);
  const now = new Date();
  const diffMs = now - start;
  
  const hours = Math.floor(diffMs / 3600000);
  const minutes = Math.floor((diffMs % 3600000) / 60000);
  const seconds = Math.floor((diffMs % 60000) / 1000);
  
  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`;
  }
  return `${minutes}m ${seconds}s`;
}

// Add a timer effect
import { useEffect, useState } from "react";

export function SimulationHeaderWithTimer({ simulation, progress }) {
  const [elapsed, setElapsed] = useState("");

  useEffect(() => {
    if (!simulation?.started_at) {
      setElapsed("");
      return;
    }
    
    const updateElapsed = () => {
      const start = new Date(simulation.started_at);
      const now = new Date();
      const diffMs = now - start;
      
      const hours = Math.floor(diffMs / 3600000);
      const minutes = Math.floor((diffMs % 3600000) / 60000);
      const seconds = Math.floor((diffMs % 60000) / 1000);
      
      if (hours > 0) {
        setElapsed(`${hours}h ${minutes}m ${seconds}s`);
      } else {
        setElapsed(`${minutes}m ${seconds}s`);
      }
    };
    
    updateElapsed();
    const interval = setInterval(updateElapsed, 1000);
    return () => clearInterval(interval);
  }, [simulation?.started_at]);

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative flex items-center justify-between gap-4 overflow-hidden border-b border-white/[0.06] bg-ink/50 p-4 backdrop-blur-xl"
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-brand-400/60 to-transparent" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="truncate font-display text-lg font-bold">{simulation?.title}</h1>
          <span
            className={`rounded-full border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ${statusColors[simulation?.status] || "border-white/15 bg-white/[0.05] text-text-faint"}`}
          >
            ● {statusLabels[simulation?.status] || simulation?.status}
          </span>
          <span className="chip text-[11px]!">
            {simulation?.role}
          </span>
          <span className="chip text-[11px]! capitalize">
            {simulation?.difficulty}
          </span>
        </div>
        <p className="mt-1 truncate text-sm text-text-muted">{simulation?.description}</p>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <div className="hidden rounded-xl border border-brand-400/25 bg-brand-500/10 px-3.5 py-1.5 text-right sm:block">
          <p className="text-[10px] uppercase tracking-wider text-text-faint">Overall Progress</p>
          <p className="gradient-text font-mono text-xl font-bold">{progress?.overall_progress || 0}%</p>
        </div>

        {simulation?.started_at && (
          <div className="hidden rounded-xl border border-white/[0.07] bg-white/[0.03] px-3.5 py-1.5 text-right md:block">
            <p className="text-[10px] uppercase tracking-wider text-text-faint">Time Elapsed</p>
            <p className="font-mono text-lg font-semibold tabular-nums">{elapsed || formatElapsedTime(simulation.started_at)}</p>
          </div>
        )}
      </div>
    </motion.div>
  );
}

const statusColors = {
  not_started: "text-text-faint",
  in_progress: "text-status-todo",
  completed: "text-status-completed",
  abandoned: "text-status-blocked",
};

const statusLabels = {
  not_started: "Not Started",
  in_progress: "In Progress",
  completed: "Completed",
  abandoned: "Abandoned",
};