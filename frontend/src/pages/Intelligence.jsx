import { useCallback, useEffect, useState } from "react";
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

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const scope = simulation?.id ? `?simulation_id=${simulation.id}` : "";
      const [a, f, r, c, s] = await Promise.all([
        apiClient.get(`/api/intelligence/analytics${scope}`),
        apiClient.get(`/api/intelligence/feedback${scope}`),
        apiClient.get(`/api/intelligence/readiness${scope}`),
        apiClient.get("/api/intelligence/certificates"),
        simulation?.id ? apiClient.get(`/api/simulations/${simulation.id}`) : Promise.resolve({ data: null }),
      ]);
      setData(a.data); setFeedback(f.data.reports?.[0] || null); setReadiness(r.data); setCertificates(c.data.certificates || []);
      setCurrentSimulation(s.data);
    } catch (e) { setMessage(e.message); }
    finally { setLoading(false); }
  }, [simulation?.id]);
  useEffect(() => { refresh(); }, [refresh]);

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

  if (loading) return <div className="p-8 text-sm text-text-muted">Loading intelligence dashboard...</div>;
  const comps = Object.entries(data?.competencies || {}).map(([name, score]) => ({ name: name.replaceAll("_", " "), score: Math.round(score) }));

  return <div className="mx-auto max-w-7xl space-y-5 p-6">
    <div className="flex items-end justify-between gap-4"><div><p className="text-[10px] uppercase tracking-[0.18em] text-status-todo">Module 4</p><h1 className="font-display text-2xl font-semibold">AI Intelligence & Readiness</h1><p className="text-sm text-text-muted">Evidence from your simulations is turned into evolving competencies, feedback and readiness analytics.</p></div><div className="flex gap-2"><button className="btn-ghost" onClick={generateFeedback}>generate feedback</button><button className="btn-ghost" onClick={recalc}>recalculate</button></div></div>
    {currentSimulation && <div className="card"><h2 className="font-display text-sm font-medium">Current internship: {currentSimulation.title}</h2><p className="mt-1 text-sm text-text-muted">Progress {Math.round(currentSimulation.progress_percentage || 0)}% · {currentSimulation.completed_tasks || 0} / {currentSimulation.total_tasks || 0} tasks · {String(currentSimulation.status || "").replaceAll("_", " ")}</p></div>}
    {message && <div className="rounded border border-status-blocked/30 bg-status-blocked/10 p-3 text-xs text-status-blocked">{message}</div>}
    {(data?.alerts || []).length > 0 && <div className="card border-status-blocked/30"><h2 className="font-display text-sm font-medium text-status-blocked">Early warning alerts</h2><div className="mt-3 space-y-2">{data.alerts.map(a=><div key={a.id} className="rounded border border-border bg-surface-2 p-3"><p className="text-xs uppercase tracking-wide text-status-blocked">{a.type.replaceAll("_"," ")}</p><p className="mt-1 text-sm text-text-muted">{a.explanation}</p><p className="mt-1 text-xs text-text-faint">Recommended: {a.recommended_action}</p></div>)}</div></div>}
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4"><Stat label="Simulations" value={data?.summary?.simulations ?? 0}/><Stat label="Evaluated interactions" value={data?.summary?.evaluated_interactions ?? 0}/><Stat label="Average score" value={data?.summary?.average_score != null ? `${Math.round(data.summary.average_score)}%` : "—"}/><Stat label="Readiness" value={readiness ? `${Math.round(readiness.readiness_score)}%` : "—"}/></div>
    <div className="grid gap-4 lg:grid-cols-2"><div className="card"><h2 className="mb-3 font-display text-sm font-medium text-text-muted">Competency profile</h2><ResponsiveContainer width="100%" height={300}><BarChart data={comps} layout="vertical" margin={{ left: 25 }}><CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-soft)"/><XAxis type="number" domain={[0,100]} hide/><YAxis type="category" dataKey="name" width={125} tick={{fill:"var(--color-text-muted)",fontSize:11}}/><Tooltip contentStyle={{background:"var(--color-surface-2)",border:"1px solid var(--color-border)"}}/><Bar dataKey="score" fill="var(--color-status-todo)" radius={[0,4,4,0]}/></BarChart></ResponsiveContainer></div>
    <div className="card"><h2 className="mb-3 font-display text-sm font-medium text-text-muted">Performance trace</h2><ResponsiveContainer width="100%" height={300}><LineChart data={data?.score_history || []}><CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-soft)"/><XAxis dataKey="session" tick={{fill:"var(--color-text-muted)",fontSize:11}}/><YAxis domain={[0,100]} tick={{fill:"var(--color-text-muted)",fontSize:11}}/><Tooltip contentStyle={{background:"var(--color-surface-2)",border:"1px solid var(--color-border)"}}/><Line type="monotone" dataKey="score" stroke="var(--color-status-completed)" strokeWidth={2} dot={{r:3}}/></LineChart></ResponsiveContainer></div></div>
    <div className="grid gap-4 lg:grid-cols-3"><div className="card lg:col-span-2"><div className="flex items-center justify-between"><h2 className="font-display text-sm font-medium text-text-muted">Latest AI feedback</h2><button className="btn-ghost" onClick={generateFeedback}>refresh</button></div>{feedback ? <><p className="mt-3 text-sm leading-6">{feedback.summary}</p><div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4"><List title="Strengths" items={feedback.strengths}/><List title="Weaknesses" items={feedback.weaknesses}/><List title="Skill gaps" items={(feedback.skill_gaps || []).map((item) => String(item).replaceAll("_", " "))}/><List title="Next focus" items={(feedback.next_focus || []).map((item) => String(item).replaceAll("_", " "))}/></div><div className="mt-4"><List title="Recommendations" items={feedback.recommendations}/></div></> : <p className="mt-3 text-sm text-text-faint">Generate a report after completing an adaptive interaction.</p>}</div>
    <div className="card"><h2 className="font-display text-sm font-medium text-text-muted">Industry readiness</h2>{readiness && <><p className="mt-3 font-mono text-4xl font-semibold">{Math.round(readiness.readiness_score)}%</p><p className="mt-1 text-sm text-text-muted">Forecast {Math.round(readiness.forecast_score)}% · percentile {Math.round(readiness.percentile)}%</p><p className="mt-3 text-xs uppercase tracking-wide text-status-todo">{readiness.trend_direction}</p><p className="mt-3 text-xs text-text-faint">Benchmark: {Math.round(readiness.benchmark_score)} · gap: {Math.round(readiness.gap_to_benchmark)}</p><button className="btn-primary mt-4 w-full" onClick={issueCertificate} disabled={readiness.readiness_level !== "PLACEMENT_READY"}>issue certificate</button></>}</div></div>
    {certificates.length > 0 && <div className="card"><h2 className="font-display text-sm font-medium text-text-muted">Certificates from completed internships</h2><div className="mt-3 space-y-2">{certificates.map(c => <div key={c.id} className="flex items-center justify-between rounded border border-border bg-surface-2 p-3"><div><p className="text-sm font-medium">{c.title}</p><p className="font-mono text-xs text-text-faint">{c.certificate_number} · {c.simulation_id === simulation?.id ? "current internship" : "completed internship"}</p></div><div className="flex items-center gap-2"><span className="text-xs text-text-muted">{new Date(c.issued_at).toLocaleDateString()}</span><button className="btn-ghost" onClick={()=>printCertificate(c)}>print</button></div></div>)}</div></div>}
  </div>;
}
function Stat({label,value}) { return <div className="card"><p className="text-xs uppercase tracking-wide text-text-faint">{label}</p><p className="mt-1 font-mono text-2xl font-semibold">{value}</p></div>; }
function List({title,items=[]}) { return <div><h3 className="text-xs uppercase tracking-wide text-text-faint">{title}</h3><ul className="mt-2 space-y-1 text-xs text-text-muted">{items.map((x,i)=><li key={i}>• {x}</li>)}</ul></div>; }
