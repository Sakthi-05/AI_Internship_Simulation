import { motion } from "framer-motion";
import {
  PieChart, Pie, Cell, ResponsiveContainer, Tooltip,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  LineChart, Line,
} from "recharts";
import { useSimulation } from "../context/SimulationContext";
import {
  useCompany, useManager, useProject, useProjectState, useSprints, useBoard,
} from "../hooks/useApi";
import { STATUS_ORDER, STATUS_META } from "../lib/status";
import LoadingScreen from "../components/LoadingScreen";

export default function Dashboard() {
  const { simulation } = useSimulation();
  const { data: company, isLoading: companyLoading } = useCompany(simulation?.companyId);
  const { data: manager } = useManager(simulation?.companyId);
  const { data: project, isLoading: projectLoading } = useProject(simulation?.projectId);
  const { data: state } = useProjectState(simulation?.projectId);
  const { data: sprints } = useSprints(simulation?.projectId);
  const { data: board, isLoading: boardLoading, error: boardError, refetch: refetchBoard } = useBoard(simulation?.projectId, simulation?.id);

  if (companyLoading || projectLoading || !company || !project) {
    if (companyLoading || projectLoading) return <LoadingScreen label="Loading dashboard" />;
    return <div className="mx-auto max-w-6xl p-6"><div className="card p-5 text-sm text-status-blocked">Dashboard data could not be loaded. Check your internship access and retry.<button className="btn-ghost ml-3" onClick={() => window.location.reload()}>Retry</button></div></div>;
  }

  if (boardLoading) return <LoadingScreen label="Loading dashboard tasks" />;
  if (boardError) return <div className="mx-auto max-w-6xl p-6"><div className="card p-5 text-sm text-status-blocked" role="alert">Task data could not be loaded: {boardError.message}<button className="btn-ghost ml-3" onClick={() => refetchBoard()}>Retry</button></div></div>;

  const totalTasks = board ? Object.values(board).reduce((sum, arr) => sum + arr.length, 0) : 0;
  const completedTasks = board?.completed?.length || 0;
  const completionPct = totalTasks ? Math.round((completedTasks / totalTasks) * 100) : 0;

  const distributionData = STATUS_ORDER.map((s) => ({
    name: STATUS_META[s].label,
    value: board?.[s]?.length || 0,
    color: STATUS_META[s].color,
  })).filter((d) => d.value > 0);

  const burndownData = (sprints || []).map((s) => ({
    name: `S${s.sprint_number}`,
    status: s.status,
  }));

  const donutData = [
    { name: "Completed", value: completedTasks, color: STATUS_META.completed.color },
    { name: "Remaining", value: totalTasks - completedTasks, color: "var(--color-surface-3)" },
  ];

  const velocity = totalTasks ? Math.round((completedTasks / Math.max(state?.current_sprint_number ?? 1, 1)) * 10) / 10 : 0;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
        className="card overflow-hidden p-6"
      >
        <div className="pointer-events-none absolute inset-x-0 top-0 h-28 bg-gradient-to-b from-brand-500/15 via-brand-500/[0.05] to-transparent" />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="eyebrow">◈ Live internship dashboard</p>
            <h1 className="mt-1.5 font-display text-2xl font-bold sm:text-3xl">
              <span className="gradient-text">{project.title}</span>
            </h1>
            <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-text-muted">
              <span className="font-medium text-text">{company.name}</span>
              <span className="text-text-faint">·</span><span>{company.industry}</span>
              <span className="text-text-faint">·</span><span>{company.size || "team"}</span>
              <span className="text-text-faint">·</span>
              <span className="chip chip-glow py-0.5!">{project.role}</span>
              <span className="chip py-0.5! capitalize">{project.difficulty}</span>
            </p>
            {(company.technology_stack || []).length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {company.technology_stack.map((t) => (
                  <span key={t} className="chip font-mono text-[11px]!">⌁ {t}</span>
                ))}
              </div>
            )}
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right">
              <p className="text-[11px] uppercase tracking-wider text-text-faint">Sprint</p>
              <p className="font-mono text-2xl font-bold text-text">{state?.current_sprint_number ?? 1}<span className="text-sm text-text-faint"> / 4</span></p>
            </div>
            <div className="relative h-16 w-16">
              <svg viewBox="0 0 64 64" className="h-full w-full -rotate-90">
                <circle cx="32" cy="32" r="26" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="7" />
                <motion.circle
                  cx="32" cy="32" r="26" fill="none"
                  stroke="url(#dashRing)" strokeWidth="7" strokeLinecap="round"
                  strokeDasharray={2 * Math.PI * 26}
                  initial={{ strokeDashoffset: 2 * Math.PI * 26 }}
                  animate={{ strokeDashoffset: 2 * Math.PI * 26 * (1 - completionPct / 100) }}
                  transition={{ duration: 1.2, ease: [0.22, 1, 0.36, 1] }}
                  style={{ filter: "drop-shadow(0 0 6px rgba(124,108,246,0.8))" }}
                />
                <defs>
                  <linearGradient id="dashRing" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor="#8b7cf6" />
                    <stop offset="100%" stopColor="#46d8ff" />
                  </linearGradient>
                </defs>
              </svg>
              <span className="absolute inset-0 flex items-center justify-center font-mono text-sm font-bold">{completionPct}%</span>
            </div>
          </div>
        </div>
      </motion.div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard index={0} icon="◈" label="Completion" value={`${completionPct}%`} accent="var(--color-status-completed)" glow="rgba(62,214,149,0.5)" />
        <StatCard index={1} icon="✓" label="Tasks completed" value={`${completedTasks} / ${totalTasks}`} accent="var(--color-brand-300)" glow="rgba(139,124,246,0.5)" />
        <StatCard index={2} icon="⚠" label="Missed deadlines" value={state?.missed_deadlines ?? 0} accent="var(--color-status-blocked)" glow="rgba(239,91,91,0.5)" />
        <StatCard index={3} icon="⚡" label="Velocity / sprint" value={`${velocity}`} accent="var(--color-accent-cyan)" glow="rgba(70,216,255,0.45)" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.4 }}
          className="card card-hover p-5"
        >
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-display text-sm font-semibold text-text-muted">Completion</h2>
            <span className="chip font-mono text-[11px]!">{completionPct}%</span>
          </div>
          <ResponsiveContainer width="100%" height={180}>
            <PieChart>
              <defs>
                <linearGradient id="donutGlow" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#8b7cf6" />
                  <stop offset="100%" stopColor="#3ed695" />
                </linearGradient>
              </defs>
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
                <Cell fill="url(#donutGlow)" stroke="none" />
                <Cell fill="rgba(255,255,255,0.07)" stroke="none" />
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
            <h2 className="font-display text-sm font-semibold text-text-muted">Task distribution</h2>
            <span className="chip font-mono text-[11px]!">{totalTasks} total</span>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={distributionData} layout="vertical" margin={{ left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" horizontal={false} />
              <XAxis type="number" hide />
              <YAxis type="category" dataKey="name" width={90} tick={{ fill: "var(--color-text-muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(139,124,246,0.08)" }} />
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
            <h2 className="font-display text-sm font-semibold text-text-muted">Sprint trajectory</h2>
            <span className="flex items-center gap-1.5 text-[11px] text-status-completed">
              <span className="h-1.5 w-1.5 rounded-full bg-status-completed shadow-[0_0_8px_rgba(62,214,149,0.9)] animate-pulse-soft" />
              live
            </span>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={burndownData}>
              <defs>
                <linearGradient id="sprintLine" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="#8b7cf6" />
                  <stop offset="100%" stopColor="#46d8ff" />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="name" tick={{ fill: "var(--color-text-muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis hide />
              <Tooltip contentStyle={tooltipStyle} formatter={(v) => [v, "status"]} />
              <Line
                type="monotone"
                dataKey={(d) => (d.status === "completed" ? 2 : d.status === "active" ? 1 : 0)}
                stroke="url(#sprintLine)"
                strokeWidth={3}
                dot={{ fill: "#46d8ff", r: 4, strokeWidth: 0, filter: undefined }}
                activeDot={{ r: 6, fill: "#46d8ff", stroke: "#fff", strokeWidth: 2 }}
                isAnimationActive
                animationDuration={1000}
              />
            </LineChart>
          </ResponsiveContainer>
        </motion.div>
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
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-accent-cyan font-display text-lg font-bold text-white shadow-[0_0_28px_-6px_rgba(124,108,246,0.9)]">
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
                    background: "linear-gradient(90deg, #3ed695, #46d8ff)",
                    boxShadow: "0 0 12px rgba(62,214,149,0.7)",
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
                        ? "border-status-completed bg-status-completed shadow-[0_0_12px_rgba(62,214,149,0.9)]"
                        : active
                          ? "border-accent-cyan bg-accent-cyan/30 shadow-[0_0_12px_rgba(70,216,255,0.9)] animate-pulse-soft"
                          : "border-white/20 bg-surface"
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
                className="flex items-start gap-2.5 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2 text-sm text-text-muted transition-colors hover:border-brand-400/25 hover:text-text"
              >
                <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-500/40 to-accent-cyan/30 font-mono text-[10px] text-brand-200">
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
  background: "rgba(20,27,48,0.95)",
  border: "1px solid rgba(139,124,246,0.35)",
  borderRadius: 12,
  fontSize: 12,
  color: "var(--color-text)",
  boxShadow: "0 16px 40px -12px rgba(0,0,0,0.8), 0 0 24px -8px rgba(124,108,246,0.5)",
  backdropFilter: "blur(12px)",
};

function StatCard({ label, value, accent, glow, icon, index = 0 }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.06 * index, duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
      whileHover={{ y: -4 }}
      className="card card-hover group overflow-hidden p-5"
    >
      <div
        className="pointer-events-none absolute -right-8 -top-8 h-28 w-28 rounded-full opacity-20 blur-2xl transition-opacity duration-300 group-hover:opacity-45"
        style={{ background: accent }}
      />
      <div className="relative flex items-center justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-faint">{label}</p>
        <span
          className="flex h-7 w-7 items-center justify-center rounded-lg text-sm"
          style={{ color: accent, background: `color-mix(in srgb, ${accent} 14%, transparent)`, boxShadow: `0 0 16px -4px ${glow}` }}
        >
          {icon}
        </span>
      </div>
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.15 + 0.06 * index }}
        className="relative mt-2 font-mono text-[26px] font-bold leading-none"
        style={{ color: accent, textShadow: `0 0 24px ${glow}` }}
      >
        {value}
      </motion.p>
    </motion.div>
  );
}
