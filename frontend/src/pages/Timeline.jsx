import { useState } from "react";
import { motion } from "framer-motion";
import { useSimulation } from "../context/SimulationContext";
import { useTimeline, useMeetings } from "../hooks/useApi";
import { useCompanyWebSocket } from "../hooks/useCompanyWebSocket";
import LoadingScreen from "../components/LoadingScreen";
import EmptyState from "../components/EmptyState";

const KIND_META = {
  task_completed: { icon: "✓", color: "var(--color-status-completed)" },
  event: { icon: "⚡", color: "var(--color-status-progress)" },
  meeting: { icon: "◷", color: "var(--color-status-review)" },
  message: { icon: "◇", color: "var(--color-status-todo)" },
};

export default function Timeline() {
  const { simulation } = useSimulation();
  useCompanyWebSocket(simulation?.companyId);

  const { data: timeline, isLoading } = useTimeline(simulation?.projectId);
  const { data: meetings, complete } = useMeetings(simulation?.projectId);
  const [notesById, setNotesById] = useState({});

  if (isLoading) return <LoadingScreen label="Loading timeline" />;

  const pendingMeetings = (meetings || []).filter((m) => !m.completed);

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
        <p className="eyebrow">◷ Story so far</p>
        <h1 className="mt-1 font-display text-2xl font-bold">Mission <span className="gradient-text">Timeline</span></h1>
        <p className="mt-1 text-sm text-text-muted">Every task, event, meeting, and message — one living record.</p>
      </motion.div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div className="lg:col-span-2">
        <div className="relative space-y-3 pl-6">
          <span className="timeline-rail absolute bottom-4 left-[9px] top-2 w-[2px] rounded-full" />
          {(timeline || []).slice().reverse().map((entry, i) => {
            const meta = KIND_META[entry.kind] || KIND_META.event;
            return (
              <motion.div
                key={`${entry.kind}-${entry.timestamp}-${i}`}
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: Math.min(i * 0.03, 0.5), type: "spring", stiffness: 260, damping: 26 }}
                whileHover={{ x: 4 }}
                className="card card-hover relative flex gap-3 p-3.5"
              >
                <span
                  className="absolute -left-6 top-4 flex h-5 w-5 items-center justify-center rounded-full border-2 border-ink text-[10px]"
                  style={{ backgroundColor: meta.color, boxShadow: `0 0 14px 1px ${meta.color}` }}
                />
                <span
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-sm"
                  style={{ color: meta.color, backgroundColor: `color-mix(in srgb, ${meta.color} 14%, transparent)` }}
                >
                  {meta.icon}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold">{entry.title}</p>
                  {entry.detail && <p className="truncate text-xs text-text-muted">{entry.detail}</p>}
                </div>
                <span className="chip shrink-0 font-mono text-[10px]!">
                  {new Date(entry.timestamp).toLocaleString(undefined, {
                    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                  })}
                </span>
              </motion.div>
            );
          })}
          {(!timeline || timeline.length === 0) && (
            <div className="card">
              <EmptyState icon="◷" title="A blank page, full of potential" hint="Ship tasks, join meetings, and chat with your team — every moment lands here." />
            </div>
          )}
        </div>
      </div>

      <div>
        <h2 className="mb-3 font-display text-base font-bold">Upcoming <span className="gradient-text">meetings</span></h2>
        <div className="space-y-3">
          {pendingMeetings.map((m, i) => (
            <motion.div
              key={m.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(i * 0.06, 0.4) }}
              className="card card-hover p-4"
            >
              <div className="flex items-center justify-between gap-2">
                <p className="font-display text-[11px] font-bold uppercase tracking-wider text-brand-300">
                  {m.meeting_type.replace(/_/g, " ")}
                </p>
                <span className="chip font-mono text-[10px]!">
                  📅 {m.scheduled_at ? new Date(m.scheduled_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "TBD"}
                </span>
              </div>
              <p className="mt-2 text-sm font-medium leading-relaxed text-text">{m.agenda}</p>
              <textarea
                className="input mt-3 rounded-xl! text-xs"
                placeholder="Meeting notes..."
                rows={2}
                value={notesById[m.id] || ""}
                onChange={(e) => setNotesById((s) => ({ ...s, [m.id]: e.target.value }))}
              />
              <motion.button
                whileTap={{ scale: 0.97 }}
                className="btn-ghost mt-2 w-full"
                disabled={complete.isPending}
                onClick={() =>
                  complete.mutateAsync({
                    meetingId: m.id,
                    notes: notesById[m.id] || "",
                    attendance: m.participants,
                    action_items: [],
                  })
                }
              >
                {complete.isPending ? "Saving…" : "✓ Mark completed"}
              </motion.button>
            </motion.div>
          ))}
          {pendingMeetings.length === 0 && (
            <div className="card">
              <EmptyState compact icon="📅" title="Calendar clear" hint="No upcoming meetings. Enjoy the focus time." />
            </div>
          )}
        </div>
      </div>
      </div>
    </div>
  );
}
