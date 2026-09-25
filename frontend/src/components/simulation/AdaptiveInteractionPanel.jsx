import { useEffect, useState } from "react";
import { apiClient } from "../../api/client";

export default function AdaptiveInteractionPanel({ simulationId }) {
  const [situation, setSituation] = useState(null);
  const [interaction, setInteraction] = useState(null);
  const [response, setResponse] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadNext = async () => {
    if (!simulationId) return;
    setLoading(true); setError("");
    try {
      const data = (await apiClient.get(`/api/intelligence/simulations/${simulationId}/adaptive/next`)).data;
      setInteraction(data.interaction);
      setSituation(data.situation);
      setResult(null);
      setResponse("");
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { loadNext(); }, [simulationId]);

  const submit = async (e) => {
    e.preventDefault();
    if (!response.trim()) return;
    setLoading(true); setError("");
    try {
      const data = (await apiClient.post(`/api/intelligence/simulations/${simulationId}/adaptive/respond`, { response })).data;
      setResult(data.evaluation);
      setInteraction(data.next_interaction);
      setSituation(data.next_situation);
      setResponse("");
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="card h-full flex flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border pb-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.18em] text-status-todo">Adaptive AI</p>
          <h3 className="font-display text-sm font-semibold">Decision Simulation</h3>
        </div>
        <button className="btn-ghost" onClick={loadNext} disabled={loading}>new situation</button>
      </div>
      <div className="flex-1 overflow-y-auto pt-3 space-y-3">
        {error && <div className="rounded border border-status-blocked/40 bg-status-blocked/10 p-2 text-xs text-status-blocked">{error}</div>}
        {situation ? (
          <>
            <div className="rounded-md bg-surface-2 p-3">
              <p className="text-xs text-text-faint">{situation.title}</p>
              <p className="mt-1 text-sm leading-6 text-text">{situation.situation}</p>
              <p className="mt-2 text-xs text-text-muted">Objective: {situation.objective}</p>
            </div>
            {situation.decision_options?.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {situation.decision_options.map((x) => <span key={x} className="rounded border border-border px-2 py-1 text-[11px] text-text-muted">{x}</span>)}
              </div>
            )}
            {result && (
              <div className="rounded-md border border-status-completed/30 bg-status-completed/5 p-3">
                <div className="flex items-center justify-between"><span className="text-xs text-text-muted">AI evaluation</span><strong className="font-mono text-lg">{Math.round(result.overall_score)}/100</strong></div>
                <p className="mt-2 text-xs leading-5 text-text-muted">{result.feedback}</p>
                <p className="mt-2 text-xs text-status-todo">Next branch: {result.branch_selected}</p>
              </div>
            )}
            <form onSubmit={submit} className="space-y-2">
              <textarea className="input min-h-24 resize-none" value={response} onChange={(e) => setResponse(e.target.value)} placeholder="Explain what you would do and why..." />
              <button className="btn-primary w-full" disabled={loading || !response.trim()}>{loading ? "AI is evaluating..." : "Submit decision"}</button>
            </form>
          </>
        ) : <p className="text-xs text-text-faint">{loading ? "Generating adaptive situation..." : "No situation available."}</p>}
      </div>
    </div>
  );
}
