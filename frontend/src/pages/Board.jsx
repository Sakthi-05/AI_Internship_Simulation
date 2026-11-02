import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { DndContext, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import { useSimulation } from "../context/SimulationContext";
import { useBoard, useUpdateTaskStatus } from "../hooks/useApi";
import { STATUS_ORDER, STATUS_META, VALID_TRANSITIONS } from "../lib/status";
import Column from "../components/Column";
import TaskDetailDrawer from "../components/TaskDetailDrawer";
import LoadingScreen from "../components/LoadingScreen";

export default function Board() {
  const { simulation } = useSimulation();
  const { data: board, isLoading, error, refetch } = useBoard(simulation?.projectId, simulation?.id);
  const updateStatus = useUpdateTaskStatus(simulation?.projectId);
  const [selectedTask, setSelectedTask] = useState(null);
  const [banner, setBanner] = useState(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } })
  );

  const allTasks = useMemo(
    () => (board ? Object.values(board).flat() : []),
    [board]
  );

  useEffect(() => {
    if (!selectedTask) return;
    const refreshed = allTasks.find((task) => task.id === selectedTask.id);
    if (refreshed && refreshed !== selectedTask) setSelectedTask(refreshed);
  }, [allTasks, selectedTask?.id]);

  if (isLoading) return <LoadingScreen label="Loading the board" />;
  if (error) return <ErrorState message={error.message} onRetry={() => refetch()} />;

  const handleDragEnd = async (event) => {
    const { active, over } = event;
    if (!over) return;
    const task = active.data.current?.task;
    const newStatus = over.id;
    if (!task || task.status === newStatus) return;

    if (task.locked) {
      setBanner("This task is locked and cannot be moved.");
      setTimeout(() => setBanner(null), 4000);
      return;
    }

    const allowed = VALID_TRANSITIONS[task.status];
    if (!allowed || !allowed.includes(newStatus)) {
      setBanner(`Cannot move from '${task.status}' to '${newStatus}'. Allowed: ${allowed ? allowed.join(", ") : "none"}`);
      setTimeout(() => setBanner(null), 4000);
      return;
    }

    try {
      await updateStatus.mutateAsync({ taskId: task.id, status: newStatus });
      setBanner(null);
    } catch (err) {
      setBanner(err.message);
      setTimeout(() => setBanner(null), 4000);
    }
  };

  const openTask = (task) => {
    // Re-find the freshest copy in case the board refetched since render.
    const fresh = allTasks.find((t) => t.id === task.id) || task;
    setSelectedTask(fresh);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 px-5 pb-1 pt-4">
        <div>
          <p className="eyebrow">Sprint command deck</p>
          <h1 className="mt-0.5 font-display text-xl font-bold">Task <span className="gradient-text">Board</span></h1>
        </div>
        <span className="chip font-mono text-[11px]!">
          <span className="h-1.5 w-1.5 rounded-full bg-status-completed shadow-[0_0_8px_rgba(62,214,149,0.9)] animate-pulse-soft" />
          {allTasks.length} tasks in play
        </span>
      </div>
      {banner && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className="mx-4 mt-2 flex items-center gap-2 rounded-xl border border-status-blocked/40 bg-status-blocked/10 px-4 py-2.5 text-sm text-status-blocked shadow-[0_0_24px_-8px_rgba(239,91,91,0.6)]"
        >
          <span>⚠</span>{banner}
        </motion.div>
      )}
      <div className="flex-1 overflow-x-auto p-4">
        <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
          <div className="flex h-full gap-3">
            {STATUS_ORDER.map((status) => (
              <Column
                key={status}
                status={status}
                meta={STATUS_META[status]}
                tasks={board?.[status] || []}
                onOpenTask={openTask}
              />
            ))}
          </div>
        </DndContext>
      </div>

      <TaskDetailDrawer
        task={selectedTask}
        allTasks={allTasks}
        onClose={() => setSelectedTask(null)}
        isUpdating={updateStatus.isPending}
        onUpdateStatus={async (payload) => {
          try {
            const updated = await updateStatus.mutateAsync(payload);
            setSelectedTask(updated);
          } catch (err) {
            setBanner(err.message);
            setTimeout(() => setBanner(null), 4000);
          }
        }}
      />
    </div>
  );
}

function ErrorState({ message, onRetry }) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        className="card max-w-sm p-8 text-center"
      >
        <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-status-blocked/15 text-xl shadow-[0_0_28px_-6px_rgba(239,91,91,0.7)]">
          ⚠
        </div>
        <p className="font-display text-sm font-semibold text-status-blocked">Couldn't load the board</p>
        <p className="mt-2 text-sm text-text-muted">{message}</p>
        <button className="btn-ghost mt-3" onClick={onRetry}>Retry</button>
      </motion.div>
    </div>
  );
}
