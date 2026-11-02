import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../api/client";
import { useSimulation } from "../context/SimulationContext";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from "recharts";

export default function Intelligence() {
  const { simulation } = useSimulation();
  const [data, setData] = useState(null);
  const [feedback, setFeedback] = useState(null);
  const [readiness, setReadiness] = useState(null);
  const [certificates, setCertificates] = useState([]);
  const [currentSimulation, setCurrentSimulation] = useState(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [sectionErrors, setSectionErrors] = useState({});
  const refreshSequence = useRef(0);
  const hasLoaded = useRef(false);

  const refresh = useCallback(async () => {
    const requestSequence = ++refreshSequence.current;
    if (!hasLoaded.current) setLoading(true);
    setMessage("");
    const scope = simulation?.id ? `?simulation_id=${simulation.id}` : "";
    const results = await Promise.allSettled([
      apiClient.get(`/api/intelligence/analytics${scope}`),
      apiClient.get(`/api/intelligence/feedback${scope}`),
      apiClient.get(`/api/intelligence/readiness${scope}`),
      apiClient.get("/api/intelligence/certificates"),
      simulation?.id ? apiClient.get(`/api/simulations/${simulation.id}`) : Promise.resolve({ data: null }),
    ]);
    // An older request may finish after a WebSocket-triggered refresh. Do not
    // let its stale snapshot overwrite the newer persisted state.
    if (requestSequence !== refreshSequence.current) return;
    const names = ["analytics", "feedback", "readiness", "certificates", "internship"];
    const nextErrors = {};
    results.forEach((result, index) => {
      if (result.status === "rejected") nextErrors[names[index]] = result.reason?.message || "Request failed.";
    });
    setSectionErrors(nextErrors);
    if (results[0].status === "fulfilled") setData(results[0].value.data);
    else setData(null);
    if (results[1].status === "fulfilled") setFeedback(results[1].value.data.reports?.[0] || null);
    if (results[2].status === "fulfilled") setReadiness(results[2].value.data);
    if (results[3].status === "fulfilled") setCertificates(results[3].value.data.certificates || []);
    if (results[4].status === "fulfilled") setCurrentSimulation(results[4].value.data);
    hasLoaded.current = true;
    setLoading(false);
  }, [simulation?.id]);
  useEffect(() => {
    hasLoaded.current = false;
    setData(null);
    setFeedback(null);
    setReadiness(null);
    setCurrentSimulation(null);
    setLoading(true);
    refresh();
  }, [refresh]);
  useEffect(() => {
    const refreshWhenUpdated = () => refresh();
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("aiwse:workspace-updated", refreshWhenUpdated);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.removeEventListener("aiwse:workspace-updated", refreshWhenUpdated);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [refresh]);

  const generateFeedback = async () => {
    setMessage("");
    try { const r = await apiClient.post(`/api/intelligence/feedback/generate${simulation?.id ? `?simulation_id=${simulation.id}` : ""}`); setFeedback(r.data); } catch (e) { setMessage(e.message); }
  };
  const recalc = async () => {
    try { const r = await apiClient.post(`/api/intelligence/readiness/recalculate${simulation?.id ? `?simulation_id=${simulation.id}` : ""}`); setReadiness(r.data); } catch (e) { setMessage(e.message); }
  };
  const issueCertificate = async () => {
    try { const r = await apiClient.post(`/api/intelligence/certificates/issue${simulation?.id ? `?simulation_id=${simulation.id}` : ""}`); setCertificates((x) => [r.data, ...x.filter(c => c.id !== r.data.id)]); } catch (e) { setMessage(e.message); }
  };
  const printCertificate = (c) => {
    const w = window.open("", "_blank");
    if (!w) return;
    w.document.write(`<html><head><title>${c.title}</title><style>body{font-family:Arial;padding:60px;background:#f5f7fb}.card{max-width:850px;margin:auto;background:white;border:8px double #243052;padding:70px;text-align:center}h1{font-size:38px}h2{font-weight:400}.number{font-family:monospace;color:#59688f}</style></head><body><div class="card"><p>AI INTERNSHIP WORKFLOW SIMULATION</p><h1>${c.title}</h1><h2>Demonstrated competencies in <strong>${c.domain}</strong></h2><p>Readiness percentile: <strong>${Math.round(c.readiness_percentile)}%</strong></p><p class="number">${c.certificate_number}</p><button onclick="window.print()">Print / Save as PDF</button></div></body></html>`);
    w.document.close();
  };

  if (loading) return <div className="mx-auto max-w-7xl space-y-3 p-6"><div className="skeleton h-24" /><div className="grid grid-cols-4 gap-3"><div className="skeleton h-20" /><div className="skeleton h-20" /><div className="skeleton h-20" /><div className="skeleton h-20" /></div><div className="skeleton h-72" /></div>;
  const comps = Object.entries(data?.competencies || {}).map(([name, score]) => ({ name: name.replaceAll("_", " "), score: Math.round(score) }));
  const readinessScore = readiness ? Math.round(readiness.readiness_score) : 0;

  return <div className="mx-auto max-w-7xl space-y-5 p-6">
    <div className="card overflow-hidden p-6">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-brand-500/15 to-transparent" />
      <div className="relative flex flex-wrap items-end justify-between gap-4">
        <div><p className="eyebrow">✦ Module 4 · AI analytics</p><h1 className="mt-1.5 font-display text-2xl font-bold sm:text-3xl">Intelligence & <span className="gradient-text">Readiness</span></h1><p className="mt-1 text-sm text-text-muted">Evidence from your simulations is turned into evolving competencies, feedback and readiness analytics.</p></div>
        <div className="flex gap-2"><button className="btn-ghost" onClick={generateFeedback}>✦ generate feedback</button><button className="btn-ghost" onClick={recalc}>↻ recalculate</button></div>
      </div>
    </div>
    {sectionErrors.internship && <SectionError title="Internship" message={sectionErrors.internship} onRetry={refresh} />}
    {currentSimulation && <div className="card flex flex-wrap items-center gap-3 p-4"><span className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-accent-cyan font-display font-bold text-white shadow-[0_0_24px_-6px_rgba(124,108,246,0.9)]">◈</span><div><h2 className="font-display text-sm font-semibold">Current internship: {currentSimulation.title}</h2><div className="mt-1.5 flex items-center gap-2"><div className="progress-track h-1.5 w-48"><div className="progress-fill" style={{ width: `${Math.round(currentSimulation.progress_percentage || 0)}%` }} /></div><p className="font-mono text-xs text-text-muted">{Math.round(currentSimulation.progress_percentage || 0)}% · {currentSimulation.completed_tasks || 0}/{currentSimulation.total_tasks || 0} · {String(currentSimulation.status || "").replaceAll("_", " ")}</p></div></div></div>}
    {message && <div className="rounded-xl border border-status-blocked/30 bg-status-blocked/10 p-3 text-xs text-status-blocked">{message}</div>}
    {(data?.alerts || []).length > 0 && <div className="card border-status-blocked/30 p-5"><h2 className="font-display text-sm font-semibold text-status-blocked">⚠ Early warning alerts</h2><div className="mt-3 grid gap-2 md:grid-cols-2">{data.alerts.map(a=><div key={a.id} className="rounded-xl border border-status-blocked/25 bg-status-blocked/[0.06] p-3"><p className="text-[11px] font-bold uppercase tracking-wider text-status-blocked">{a.type.replaceAll("_"," ")}</p><p className="mt-1 text-sm text-text-muted">{a.explanation}</p><p className="mt-1 text-xs text-text-faint">Recommended: {a.recommended_action}</p></div>)}</div></div>}
    {sectionErrors.analytics && <SectionError title="Performance analytics" message={sectionErrors.analytics} onRetry={refresh} />}
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5"><Stat icon="◈" label="Simulations" value={data?.summary?.simulations != null ? data.summary.simulations : "—"} accent="var(--color-brand-300)"/><Stat icon="⌘" label="Technical score" value={data?.summary?.technical_score != null ? `${Math.round(data.summary.technical_score)}%` : "—"} accent="var(--color-accent-cyan)"/><Stat icon="◇" label="Behavioral score" value={data?.summary?.behavioral_score != null ? `${Math.round(data.summary.behavioral_score)}%` : "—"} accent="var(--color-status-progress)"/><Stat icon="⬆" label="Current difficulty" value={data?.current_difficulty || "—"} accent="var(--color-brand-300)"/><Stat icon="🚀" label="Readiness" value={readiness ? `${Math.round(readiness.readiness_score)}%` : "—"} accent="var(--color-status-completed)"/></div>
    {data?.latest_adaptation && <section className="card p-5"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-display text-sm font-semibold text-text-muted">Latest technical adaptation</h2><span className="chip capitalize">{data.latest_adaptation.previous_difficulty} → {data.latest_adaptation.next_difficulty}</span></div><p className="mt-2 text-sm text-text-muted">{data.latest_adaptation.reason}</p><p className="mt-2 text-xs text-text-faint">Score {Math.round(data.latest_adaptation.technical_score)}% · evidence source: {data.latest_adaptation.evaluation_source}</p></section>}
    {simulation?.id && <section className="card p-5" aria-live="polite"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-display text-sm font-semibold text-text-muted">Career insights</h2><span className="chip capitalize">{data?.career_insights?.status || (data || sectionErrors.analytics ? "unavailable" : "pending")}</span></div>{data?.career_insights?.status === "available" ? <><p className="mt-2 text-xs text-text-faint">Evidence-backed results from this simulation. Trends require multiple comparable recorded interactions.</p><div className="mt-3 grid gap-3 md:grid-cols-2"><InsightList title="Supported strengths" items={data.career_insights.strengths} /><InsightList title="Supported growth areas" items={data.career_insights.competency_gaps} /></div>{data.career_insights.trends?.length > 0 && <div className="mt-3"><h3 className="text-[11px] font-bold uppercase tracking-wider text-text-faint">Recorded trends</h3><ul className="mt-2 flex flex-wrap gap-2">{data.career_insights.trends.map((trend) => <li key={trend.dimension} className="chip capitalize">{trend.dimension.replaceAll("_", " ")} · {trend.direction} ({trend.observations} records)</li>)}</ul></div>}{data.career_insights.recommendations?.length > 0 && <div className="mt-3"><List title="Existing learning recommendations" items={data.career_insights.recommendations} color="var(--color-brand-300)" /></div>}</> : <p className="mt-2 text-sm text-text-faint">{data?.career_insights?.status === "insufficient_evidence" ? "There is not enough recorded competency evidence to support insights yet." : data?.career_insights?.status === "unavailable" || sectionErrors.analytics || data ? "Career insights are unavailable right now. Other analytics remain available." : "Career insights are loading."}</p>}</section>}
    <div className="grid gap-4 lg:grid-cols-2"><div className="card card-hover p-5"><h2 className="mb-3 font-display text-sm font-semibold text-text-muted">Competency profile</h2><ResponsiveContainer width="100%" height={300}><BarChart data={comps} layout="vertical" margin={{ left: 25 }}><defs><linearGradient id="compBar" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stopColor="#5f4de8"/><stop offset="100%" stopColor="#46d8ff"/></linearGradient></defs><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)"/><XAxis type="number" domain={[0,100]} hide/><YAxis type="category" dataKey="name" width={125} tick={{fill:"var(--color-text-muted)",fontSize:11}} axisLine={false} tickLine={false}/><Tooltip contentStyle={glassTooltip}/><Bar dataKey="score" fill="url(#compBar)" radius={[0,6,6,0]} isAnimationActive animationDuration={900}/></BarChart></ResponsiveContainer></div>
    <div className="card card-hover p-5"><div className="mb-3 flex items-center justify-between"><h2 className="font-display text-sm font-semibold text-text-muted">Performance trace</h2><span className="flex items-center gap-1.5 text-[11px] text-status-completed"><span className="h-1.5 w-1.5 rounded-full bg-status-completed shadow-[0_0_8px_rgba(62,214,149,0.9)] animate-pulse-soft" />live</span></div><ResponsiveContainer width="100%" height={300}><LineChart data={data?.score_history || []}><defs><linearGradient id="perfLine" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stopColor="#3ed695"/><stop offset="100%" stopColor="#46d8ff"/></linearGradient></defs><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)"/><XAxis dataKey="session" tick={{fill:"var(--color-text-muted)",fontSize:11}} axisLine={false} tickLine={false}/><YAxis domain={[0,100]} tick={{fill:"var(--color-text-muted)",fontSize:11}} axisLine={false} tickLine={false}/><Tooltip contentStyle={glassTooltip}/><Line type="monotone" dataKey="score" stroke="url(#perfLine)" strokeWidth={3} dot={{r:3,fill:"#3ed695",strokeWidth:0}} activeDot={{r:5,fill:"#3ed695",stroke:"#fff",strokeWidth:2}} isAnimationActive animationDuration={1000}/></LineChart></ResponsiveContainer></div></div>
    <div className="grid gap-4 lg:grid-cols-3"><div className="card p-5 lg:col-span-2"><div className="flex items-center justify-between"><h2 className="font-display text-sm font-semibold text-text-muted">✦ Latest AI feedback</h2><button className="btn-ghost" onClick={generateFeedback}>refresh</button></div>{sectionErrors.feedback && <SectionError title="Feedback" message={sectionErrors.feedback} onRetry={refresh} />}{feedback ? <><p className="mt-3 rounded-xl border border-brand-400/20 bg-brand-500/[0.06] p-4 text-sm leading-6">{feedback.summary}</p><div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4"><List title="Strengths" items={feedback.strengths} color="var(--color-status-completed)"/><List title="Weaknesses" items={feedback.weaknesses} color="var(--color-status-progress)"/><List title="Skill gaps" items={(feedback.skill_gaps || []).map((item) => String(item).replaceAll("_", " "))} color="var(--color-status-blocked)"/><List title="Next focus" items={(feedback.next_focus || []).map((item) => String(item).replaceAll("_", " "))} color="var(--color-accent-cyan)"/></div><div className="mt-4"><List title="Recommendations" items={feedback.recommendations} color="var(--color-brand-300)"/></div></> : <p className="mt-3 text-sm text-text-faint">Generate a report after completing an adaptive interaction.</p>}</div>
    <div className="card overflow-hidden p-5 text-center"><div className="pointer-events-none absolute inset-x-0 top-0 h-20 bg-gradient-to-b from-status-completed/10 to-transparent" /><h2 className="relative font-display text-sm font-semibold text-text-muted">Industry readiness</h2>{sectionErrors.readiness && <SectionError title="Readiness" message={sectionErrors.readiness} onRetry={refresh} />}{readiness && <div className="relative"><div className="relative mx-auto mt-2 h-36 w-36"><svg viewBox="0 0 100 100" className="h-full w-full -rotate-90"><circle cx="50" cy="50" r="42" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="9"/><circle cx="50" cy="50" r="42" fill="none" stroke="url(#readyRing)" strokeWidth="9" strokeLinecap="round" strokeDasharray={2 * Math.PI * 42} strokeDashoffset={2 * Math.PI * 42 * (1 - readinessScore / 100)} style={{ filter: "drop-shadow(0 0 8px rgba(62,214,149,0.8))", transition: "stroke-dashoffset 1.2s cubic-bezier(0.22,1,0.36,1)" }}/><defs><linearGradient id="readyRing" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor="#3ed695"/><stop offset="100%" stopColor="#46d8ff"/></linearGradient></defs></svg><span className="absolute inset-0 flex items-center justify-center font-mono text-2xl font-bold">{readinessScore}%</span></div><p className="mt-2 text-sm text-text-muted">Forecast {Math.round(readiness.forecast_score)}% · percentile {Math.round(readiness.percentile)}%</p><p className="chip chip-glow mx-auto mt-2 w-fit font-mono text-[11px]!">{readiness.trend_direction}</p><p className="mt-2 text-xs text-text-faint">Benchmark: {Math.round(readiness.benchmark_score)} · gap: {Math.round(readiness.gap_to_benchmark)}</p><button className="btn-primary mt-4 w-full" onClick={issueCertificate} disabled={readiness.readiness_level !== "PLACEMENT_READY"}>🏆 issue certificate</button></div>}</div></div>
    {(sectionErrors.certificates || certificates.length > 0) && <div className="card p-5"><h2 className="font-display text-sm font-semibold text-text-muted">🏆 Certificates from completed internships</h2>{sectionErrors.certificates ? <SectionError title="Certificates" message={sectionErrors.certificates} onRetry={refresh} /> : <div className="mt-3 space-y-2">{certificates.map(c => <div key={c.id} className="flex items-center justify-between gap-3 rounded-xl border border-amber-300/20 bg-gradient-to-r from-amber-300/[0.07] to-transparent p-3 transition-all hover:border-amber-300/40"><div className="flex items-center gap-3"><span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-amber-300 to-amber-500 text-lg shadow-[0_0_20px_-6px_rgba(252,211,77,0.8)]">🏆</span><div><p className="text-sm font-semibold">{c.title}</p><p className="font-mono text-xs text-text-faint">{c.certificate_number} · {c.simulation_id === simulation?.id ? "current internship" : "completed internship"}</p></div></div><div className="flex shrink-0 items-center gap-2"><span className="hidden text-xs text-text-muted sm:block">{new Date(c.issued_at).toLocaleDateString()}</span><button className="btn-ghost" onClick={()=>printCertificate(c)}>print</button></div></div>)}</div>}</div>}
  </div>;
}
const glassTooltip = {
  background: "rgba(20,27,48,0.95)",
  border: "1px solid rgba(139,124,246,0.35)",
  borderRadius: 12,
  fontSize: 12,
  color: "var(--color-text)",
  boxShadow: "0 16px 40px -12px rgba(0,0,0,0.8)",
  backdropFilter: "blur(12px)",
};
function Stat({label,value,icon,accent}) { return <div className="card card-hover group relative overflow-hidden p-4"><div className="pointer-events-none absolute -right-6 -top-6 h-20 w-20 rounded-full opacity-15 blur-2xl transition-opacity group-hover:opacity-40" style={{ background: accent }} /><p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-text-faint"><span style={{ color: accent }}>{icon}</span>{label}</p><p className="mt-1.5 font-mono text-2xl font-bold" style={{ color: accent }}>{value}</p></div>; }
function List({title,items=[],color}) { return <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] p-3"><h3 className="text-[11px] font-bold uppercase tracking-wider" style={{ color: color || "var(--color-text-faint)" }}>{title}</h3><ul className="mt-2 space-y-1.5 text-xs leading-relaxed text-text-muted">{items.map((x,i)=><li key={i} className="flex gap-1.5"><span style={{ color: color || "var(--color-brand-300)" }}>▸</span><span>{x}</span></li>)}</ul></div>; }
function InsightList({ title, items = [] }) { return <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] p-3"><h3 className="text-[11px] font-bold uppercase tracking-wider text-text-faint">{title}</h3>{items.length ? <ul className="mt-2 space-y-2 text-xs text-text-muted">{items.map((item) => <li key={item.dimension}><span className="capitalize">{item.dimension.replaceAll("_", " ")}</span>{item.score != null && <span className="ml-2 font-mono text-text-faint">{Math.round(item.score)}%</span>}<span className="ml-2 text-text-faint">· {item.evidence_status.replaceAll("_", " ")}</span></li>)}</ul> : <p className="mt-2 text-xs text-text-faint">No supported findings recorded.</p>}</div>; }
function SectionError({ title, message, onRetry }) { return <div className="mt-3 rounded-xl border border-status-blocked/25 bg-status-blocked/[0.06] p-3 text-left text-xs text-status-blocked" role="alert"><span>{title} could not be loaded: {message}</span><button className="btn-ghost ml-2" onClick={onRetry}>Retry</button></div>; }
