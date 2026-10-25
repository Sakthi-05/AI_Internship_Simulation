import { useDroppable } from "@dnd-kit/core";
import { AnimatePresence } from "framer-motion";
import TaskCard from "./TaskCard";

export default function Column({ status, meta, tasks, onOpenTask }) {
  const { setNodeRef, isOver } = useDroppable({ id: status });

  return (
    <div
      ref={setNodeRef}
      className={`board-column relative flex w-72 shrink-0 flex-col overflow-hidden rounded-xl border transition-all duration-200 ${
        isOver
          ? "board-column-active"
          : ""
      }`}
    >
      <span
        className="absolute inset-x-0 top-0 h-[3px] opacity-80"
        style={{ background: meta.color }}
      />
      <div className="board-column-heading flex items-center gap-2 border-b px-3 py-3">
        <span
          className="h-2 w-2 rounded-full"
          style={{ backgroundColor: meta.color }}
        />
        <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-text-muted">
          {meta.label}
        </h3>
        <span
          className="ml-auto rounded-full px-2 py-0.5 font-mono text-[10px] font-bold"
          style={{ color: meta.color, backgroundColor: `color-mix(in srgb, ${meta.color} 14%, transparent)` }}
        >
          {tasks.length}
        </span>
      </div>
      <div className="min-h-[120px] flex-1 space-y-2 overflow-y-auto p-2">
        <AnimatePresence>
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} onOpen={onOpenTask} />
          ))}
        </AnimatePresence>
        {tasks.length === 0 && (
          <div className="mx-1 rounded-xl border border-dashed border-border bg-white/60 px-2 py-8 text-center">
            <p className="text-lg opacity-40">○</p>
            <p className="mt-1 text-xs text-text-faint">Drop tasks here</p>
          </div>
        )}
      </div>
    </div>
  );
}
