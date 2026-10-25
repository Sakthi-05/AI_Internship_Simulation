import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import {
  PieChart, Pie, Cell, ResponsiveContainer, Tooltip,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  LineChart, Line,
} from "recharts";
import { useSimulation } from "../context/SimulationContext";
import {
  useCompany, useManager, useProject, useProjectState, useSprints, useBoard, useTimeline,
} from "../hooks/useApi";
import { STATUS_ORDER, STATUS_META, PRIORITY_META } from "../lib/status";
import LoadingScreen from "../components/LoadingScreen";

export default function Dashboard() {
  const { simulation } = useSimulation();
  const { data: company, isLoading: companyLoading } = useCompany(simulation?.companyId);
  const { data: manager } = useManager(simulation?.companyId);
  const { data: project, isLoading: projectLoading } = useProject(simulation?.projectId);
  const { data: state } = useProjectState(simulation?.projectId);
  const { data: sprints } = useSprints(simulation?.projectId);
  const { data: board, isLoading: boardLoading, error: boardError, refetch: refetchBoard } = useBoard(simulation?.projectId, simulation?.id);
  const { data: timeline } = useTimeline(simulation?.projectId);

  if (companyLoading || projectLoading || !company || !project) {
    if (companyLoading || projectLoading) return <LoadingScreen label="Loading dashboard" />;
    return <div className="mx-auto max-w-6xl p-6"><div className="card p-5 text-sm text-status-blocked">Dashboard data could not be loaded. Check your internship access and retry.<button className="btn-ghost ml-3" onClick={() => window.location.reload()}>Retry</button></div></div>;
  }

  if (boardLoading) return <LoadingScreen label="Loading dashboard tasks" />;
  if (boardError) return <div className="mx-auto max-w-6xl p-6"><div className="card p-5 text-sm text-status-blocked" role="alert">Task data could not be loaded: {boardError.message}<button className="btn-ghost ml-3" onClick={() => refetchBoard()}>Retry</button></div></div>;

  const totalTasks = board ? Object.values(board).reduce((sum, arr) => sum + arr.length, 0) : 0;
  const completedTasks = board?.completed?.length || 0;
  const completionPct = totalTasks ? Math.round((completedTasks / totalTasks) * 100) : 0;

  const chartColors = { backlog: "#94A3B8", todo: "#2563EB", in_progress: "#D97706", submitted: "#8B5CF6", under_review: "#6366F1", blocked: "#F97373", completed: "#0D9488" };
  const distributionData = STATUS_ORDER.map((s) => ({
    name: STATUS_META[s].label,
    value: board?.[s]?.length || 0,
    color: chartColors[s],
  })).filter((d) => d.value > 0);

  const burndownData = (sprints || []).map((s) => ({
    name: `S${s.sprint_number}`,
    status: s.status,
  }));

  const donutData = [
    { name: "Completed", value: completedTasks, color: "#0D9488" },
    { name: "Remaining", value: totalTasks - completedTasks, color: "#E5E7EB" },
  ];

  const velocity = totalTasks ? Math.round((completedTasks / Math.max(state?.current_sprint_number ?? 1, 1)) * 10) / 10 : 0;
  const recentTasks = STATUS_ORDER.flatMap((status) => (board?.[status] || []).map((task) => ({ ...task, status })))
    .filter((task) => task.status !== "completed")
    .sort((a, b) => (a.sequence ?? Number.MAX_SAFE_INTEGER) - (b.sequence ?? Number.MAX_SAFE_INTEGER))
    .slice(0, 4);
  const recentActivity = [...(timeline || [])].slice(-5).reverse();

  return (
    <div className="dashboard-page mx-auto max-w-6xl space-y-5 p-6 sm:p-7">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
        className="dashboard-hero card overflow-hidden p-6 sm:p-7"
      >
        <div className="hero-decoration" aria-hidden="true"><span/><span/><span/></div>
        <div className="relative flex flex-wrap items-center justify-between gap-6">
          <div className="hero-copy">
            <p className="eyebrow"><span className="eyebrow-mark">✦</span> Internship overview</p>
            <h1 className="mt-2 font-display text-2xl font-bold tracking-tight sm:text-3xl">{project.title}</h1>
            <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-text-muted">
              <span className="font-semibold text-text">{company.name}</span>
              {company.industry && <><span className="text-text-faint">·</span><span>{company.industry}</span></>}
              {company.size && <><span className="text-text-faint">·</span><span>{company.size}</span></>}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="chip chip-glow">{project.role}</span>
              <span className="chip capitalize">{project.difficulty} track</span>
            </div>
            {(company.technology_stack || []).length > 0 && (
              <div className="mt-4 flex flex-wrap gap-1.5">
                {company.technology_stack.map((t) => (
                  <span key={t} className="tech-chip"><span className="tech-dot"/>{t}</span>
                ))}
              </div>
            )}
          </div>
          <div className="hero-progress">
            <div className="hero-progress-copy"><span className="hero-progress-label">Overall progress</span><strong>{completedTasks}<small> / {totalTasks} tasks</small></strong><span className="hero-progress-sprint">Sprint {state?.current_sprint_number ?? 1} <i>·</i> {sprints ? sprints.length : "—"} planned</span></div>
            <div className="relative h-[92px] w-[92px] shrink-0">
              <svg viewBox="0 0 64 64" className="h-full w-full -rotate-90">
                <circle cx="32" cy="32" r="26" fill="none" stroke="#E5E7EB" strokeWidth="6" />
                <motion.circle
                  cx="32" cy="32" r="26" fill="none"
                  stroke="#2563EB" strokeWidth="6" strokeLinecap="round"
                  strokeDasharray={2 * Math.PI * 26}
                  initial={{ strokeDashoffset: 2 * Math.PI * 26 }}
                  animate={{ strokeDashoffset: 2 * Math.PI * 26 * (1 - completionPct / 100) }}
                  transition={{ duration: 1.2, ease: [0.22, 1, 0.36, 1] }}
                  style={{ filter: "drop-shadow(0 2px 4px rgba(37,99,235,0.25))" }}
                />
              </svg>
              <span className="absolute inset-0 flex items-center justify-center font-mono text-[15px] font-bold">{completionPct}<small className="ml-0.5 text-[9px] font-semibold text-text-muted">%</small></span>
            </div>
          </div>
        </div>
      </motion.div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard index={0} icon="target" label="Completion" value={`${completionPct}%`} accent="#0D9488" hint="Tasks completed" />
        <StatCard index={1} icon="check" label="Tasks completed" value={`${completedTasks} / ${totalTasks}`} accent="#4F46E5" hint="Across all sprints" />
        <StatCard index={2} icon="alert" label="Missed deadlines" value={state?.missed_deadlines ?? 0} accent="#E76F51" hint="Needs attention" />
        <StatCard index={3} icon="trend" label="Velocity / sprint" value={`${velocity}`} accent="#0891B2" hint="Completed per sprint" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.4 }}
          className="card card-hover p-5"
        >
          <div className="mb-3 flex items-center justify-between">
            <div><h2 className="font-display text-sm font-semibold text-text">Task completion</h2><p className="mt-1 text-[11px] text-text-faint">Progress across your internship</p></div>
            <span className="chip font-mono text-[11px]!">{completionPct}%</span>
          </div>
          <ResponsiveContainer width="100%" height={180}>
            <PieChart>
              <Pie
                data={donutData}
                dataKey="value"
                innerRadius={52}
                outerRadius={76}
                paddingAngle={3}
                cornerRadius={6}
                strokeWidth={0}
                isAnimationActive
              >
                <Cell fill="#0D9488" stroke="none" />
                <Cell fill="#E5E7EB" stroke="none" />
              </Pie>
              <Tooltip contentStyle={tooltipStyle} />
            </PieChart>
          </ResponsiveContainer>
          <div className="progress-track mt-2 h-2">
            <motion.div
              className="progress-fill"
              initial={{ width: 0 }}
              animate={{ width: `${completionPct}%` }}
              transition={{ duration: 1, ease: [0.22, 1, 0.36, 1], delay: 0.3 }}
            />
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.22, duration: 0.4 }}
          className="card card-hover p-5"
        >
          <div className="mb-3 flex items-center justify-between">
            <div><h2 className="font-display text-sm font-semibold text-text">Task distribution</h2><p className="mt-1 text-[11px] text-text-faint">Current workflow by status</p></div>
            <span className="chip font-mono text-[11px]!">{totalTasks} total</span>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={distributionData} layout="vertical" margin={{ left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#EEF0F3" horizontal={false} />
              <XAxis type="number" hide />
              <YAxis type="category" dataKey="name" width={90} tick={{ fill: "var(--color-text-muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "#F4F5F7" }} />
              <Bar dataKey="value" radius={[0, 6, 6, 0]} isAnimationActive animationDuration={900}>
                {distributionData.map((d, i) => (
                  <Cell key={i} fill={d.color} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.29, duration: 0.4 }}
          className="card card-hover p-5"
        >
          <div className="mb-3 flex items-center justify-between">
            <div><h2 className="font-display text-sm font-semibold text-text">Sprint trajectory</h2><p className="mt-1 text-[11px] text-text-faint">Status of planned sprints</p></div>
            <span className="rounded-full bg-[#EEF2FF] px-2 py-1 text-[10px] font-semibold text-[#4F46E5]">{burndownData.length} sprints</span>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={burndownData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#EEF0F3" />
              <XAxis dataKey="name" tick={{ fill: "var(--color-text-muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis hide />
              <Tooltip contentStyle={tooltipStyle} formatter={(v) => [v, "status"]} />
              <Line
                type="monotone"
                dataKey={(d) => (d.status === "completed" ? 2 : d.status === "active" ? 1 : 0)}
                stroke="#6366F1"
                strokeWidth={2.5}
                dot={{ fill: "#06B6D4", r: 4, stroke: "#fff", strokeWidth: 2 }}
                activeDot={{ r: 6, fill: "#6366F1", stroke: "#fff", strokeWidth: 2 }}
                isAnimationActive
                animationDuration={1000}
              />
            </LineChart>
          </ResponsiveContainer>
        </motion.div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.15fr_.85fr]">
        <section className="card p-5">
          <div className="mb-4 flex items-center justify-between gap-3"><div><p className="eyebrow">Your workload</p><h2 className="mt-1 font-display text-base font-semibold">Tasks in progress</h2><p className="mt-1 text-xs text-text-faint">A quick look at the next items on your board.</p></div><Link to="/board" className="btn-ghost">Open board <span aria-hidden="true">↗</span></Link></div>
          {recentTasks.length ? <div className="dashboard-task-list">{recentTasks.map((task) => <article className="dashboard-task-row" key={task.id}><span className="task-status-marker" style={{ backgroundColor: chartColors[task.status] || "#94A3B8" }}/><div className="min-w-0 flex-1"><h3 className="truncate text-sm font-semibold">{task.title}</h3><p className="mt-1 text-[11px] text-text-faint">#{task.sequence} <span className="mx-1">·</span> {task.estimated_hours}h estimated</p></div><div className="flex shrink-0 flex-col items-end gap-1"><span className="task-status-pill" style={{ color: chartColors[task.status] || "#64748B", backgroundColor: `color-mix(in srgb, ${chartColors[task.status] || "#64748B"} 10%, white)` }}>{STATUS_META[task.status]?.label || task.status.replaceAll("_", " ")}</span><span className="text-[10px] text-text-faint">{PRIORITY_META[task.priority]?.label || "Priority not set"}</span></div></article>)}</div> : <p className="rounded-lg bg-surface-3 px-4 py-6 text-center text-sm text-text-muted">No open tasks are currently on your board.</p>}
        </section>

        <section className="card p-5">
          <div className="mb-4"><p className="eyebrow">Latest updates</p><h2 className="mt-1 font-display text-base font-semibold">Activity timeline</h2><p className="mt-1 text-xs text-text-faint">Recent events from your internship workspace.</p></div>
          {recentActivity.length ? <div className="dashboard-activity-list">{recentActivity.map((event, index) => <article className="dashboard-activity-row" key={`${event.kind}-${event.timestamp}-${index}`}><span className={`activity-marker activity-marker-${index % 4}`}><span/></span><div className="min-w-0 flex-1"><h3 className="truncate text-xs font-semibold">{event.title}</h3>{event.detail && <p className="mt-1 line-clamp-1 text-[11px] text-text-faint">{event.detail}</p>}<time className="mt-1 block text-[10px] text-text-faint">{event.timestamp ? new Date(event.timestamp).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : ""}</time></div></article>)}</div> : <p className="rounded-lg bg-surface-3 px-4 py-6 text-center text-sm text-text-muted">Activity will appear here as you work through the simulation.</p>}
        </section>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.34, duration: 0.4 }}
          className="card card-hover overflow-hidden p-5"
        >
          <div className="pointer-events-none absolute inset-x-0 top-0 h-16 bg-gradient-to-b from-brand-500/10 to-transparent" />
          <h2 className="eyebrow relative">Your manager</h2>
          {manager ? (
            <div className="relative mt-3 flex items-center gap-3 text-sm">
              <div className="manager-avatar">
                {manager.name?.[0] || "M"}
              </div>
              <div className="min-w-0">
                <p className="truncate font-semibold">{manager.name}</p>
                <p className="truncate text-xs text-text-muted">{manager.title} · {manager.personality.replace("_", " ")}</p>
              </div>
            </div>
          ) : (
            <p className="relative mt-3 text-sm text-text-faint">No manager assigned.</p>
          )}
          {manager && (
            <div className="relative mt-4">
              <div className="mb-1.5 flex items-center justify-between text-xs">
                <span className="text-text-faint">Satisfaction</span>
                <span className="font-mono font-semibold">{manager.satisfaction_score}/100</span>
              </div>
              <div className="progress-track h-2">
                <motion.div
                  className="h-full rounded-full"
                  style={{
                    background: "#0D9488",
                  }}
                  initial={{ width: 0 }}
                  animate={{ width: `${manager.satisfaction_score}%` }}
                  transition={{ duration: 1, ease: [0.22, 1, 0.36, 1], delay: 0.45 }}
                />
              </div>
            </div>
          )}
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4, duration: 0.4 }}
          className="card p-5"
        >
          <h2 className="eyebrow">Sprint roadmap</h2>
          {(sprints || []).length === 0 && <p className="mt-3 text-sm text-text-faint">No sprints have been planned.</p>}
          <ul className="relative mt-4 space-y-4 text-sm">
            <span className="timeline-rail absolute bottom-2 left-[7px] top-2 w-[2px] rounded-full opacity-60" />
            {(sprints || []).map((sprint, i) => {
              const done = sprint.status === "completed";
              const active = sprint.status === "active";
              return (
                <motion.li
                  key={sprint.id}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.45 + i * 0.07 }}
                  className="relative pl-7"
                >
                  <span
                    className={`absolute left-0 top-1.5 h-[16px] w-[16px] rounded-full border-2 ${
                      done
                        ? "border-status-completed bg-status-completed"
                        : active
                          ? "border-brand-500 bg-brand-500/20"
                          : "border-border bg-surface"
                    }`}
                  />
                  <p className="font-semibold leading-tight">{sprint.name}</p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-text-muted">{sprint.goal}</p>
                  <p className="mt-1 font-mono text-[11px] uppercase tracking-wide text-text-faint">
                    {sprint.status}{sprint.start_date ? ` · ${sprint.start_date.slice(0, 10)} → ${sprint.end_date?.slice(0, 10) || ""}` : ""}
                  </p>
                </motion.li>
              );
            })}
          </ul>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.46, duration: 0.4 }}
          className="card p-5 md:col-span-2 xl:col-span-1"
        >
          <h2 className="eyebrow">Mission objectives</h2>
          <ul className="mt-3 space-y-2">
            {project.objectives.map((o, i) => (
              <motion.li
                key={o}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.5 + i * 0.05 }}
                className="objective-row flex items-start gap-2.5 rounded-xl border px-3 py-2 text-sm text-text-muted transition-colors hover:border-brand-400/25 hover:text-text"
              >
                <span className="objective-number mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full font-mono text-[10px] text-brand-500">
                  {i + 1}
                </span>
                {o}
              </motion.li>
            ))}
          </ul>
        </motion.div>
      </div>
    </div>
  );
}

const tooltipStyle = {
  background: "#FFFFFF",
  border: "1px solid #E5E7EB",
  borderRadius: 10,
  fontSize: 12,
  color: "#1E293B",
  boxShadow: "0 8px 24px rgba(15,23,42,0.12)",
};

function StatCard({ label, value, accent, icon, hint, index = 0 }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.06 * index, duration: 0.28 }}
      whileHover={{ y: -2 }}
      className="dashboard-stat card card-hover group overflow-hidden p-4"
    >
      <div className="relative flex items-center gap-3">
        <span className="stat-icon" style={{ color: accent, background: `color-mix(in srgb, ${accent} 12%, white)` }}><DashboardIcon name={icon}/></span>
        <div className="min-w-0"><p className="text-[11px] font-medium text-text-muted">{label}</p>
          <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 + 0.05 * index }} className="mt-1 font-display text-[25px] font-bold leading-none tracking-tight text-text">{value}</motion.p>
          <p className="mt-1.5 text-[10px] text-text-faint">{hint}</p>
        </div>
      </div>
    </motion.div>
  );
}

function DashboardIcon({ name }) {
  const paths = {
    target: <><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/></>,
    check: <><path d="m5 12 4 4L19 6"/><path d="M20 12a8 8 0 1 1-4.1-7"/></>,
    alert: <><path d="M10.3 3.9 2.8 17a2 2 0 0 0 1.7 3h15a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 3h.01"/></>,
    trend: <><path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
