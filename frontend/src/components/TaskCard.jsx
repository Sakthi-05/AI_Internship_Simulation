import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { motion } from "framer-motion";
import { PRIORITY_META } from "../lib/status";

export default function TaskCard({ task, onOpen }) {
  const isLocked = task.locked === true;

  const draggable = useDraggable({
    id: task.id,
    data: { task },
    disabled: isLocked,
  });

  const style = {
    transform: CSS.Translate.toString(draggable.transform),
  };

  const priority = PRIORITY_META[task.priority];
  const overdue =
    task.deadline && task.status !== "completed" && new Date(task.deadline) < new Date();

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      whileHover={isLocked ? undefined : { y: -2 }}
      transition={{ type: "spring", stiffness: 400, damping: 28 }}
      ref={draggable.setNodeRef}
      style={style}
      {...(isLocked ? {} : { ...draggable.listeners, ...draggable.attributes })}
      onClick={() => onOpen(task)}
      className={`task-card group select-none rounded-xl border p-3 text-sm transition-all duration-200 ${
        isLocked
          ? "task-card-locked cursor-not-allowed opacity-65"
          : "cursor-grab active:cursor-grabbing"
      } ${draggable.isDragging ? "rotate-2 scale-105 opacity-70 shadow-[0_20px_50px_-12px_rgba(124,108,246,0.7)]" : ""} ${overdue ? "border-status-blocked/40" : ""}`}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[10px] text-text-faint font-mono">#{task.sequence}</span>
        {isLocked ? (
          <svg className="w-3.5 h-3.5 text-text-faint" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
        ) : (
          <span
            className="rounded px-1.5 py-0.5 text-[10px] font-mono uppercase tracking-wide"
            style={{ color: priority.color, backgroundColor: `color-mix(in srgb, ${priority.color} 15%, transparent)` }}
          >
            {priority.label}
          </span>
        )}
        {!isLocked && task.depends_on_task_ids?.length > 0 && (
          <span className="text-[10px] text-text-faint" title="Has dependencies">
            ⛓ {task.depends_on_task_ids.length}
          </span>
        )}
      </div>
      <p className={`font-medium leading-snug transition-colors ${isLocked ? "text-text-muted" : "text-text"}`}>{task.title}</p>
      <div className="mt-2 flex items-center justify-between font-mono text-[11px] text-text-faint">
        <span className="task-estimate rounded px-1.5 py-0.5">{task.estimated_hours}h est.</span>
        {task.deadline && (
          <span className={overdue ? "animate-pulse-soft rounded bg-status-blocked/15 px-1.5 py-0.5 font-bold text-status-blocked" : ""}>
            {overdue ? "⚠ " : ""}{new Date(task.deadline).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          </span>
        )}
      </div>
    </motion.div>
  );
}
