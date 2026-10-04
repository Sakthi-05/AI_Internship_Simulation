import { useCallback, useEffect, useState } from "react";
import { apiClient } from "../../api/client";

export default function AdaptiveInteractionPanel({ simulationId }) {
  const [situation, setSituation] = useState(null);
  const [response, setResponse] = useState("");
  const [result, setResult] = useState(null);
  const [selectedChoice, setSelectedChoice] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadNext = useCallback(async () => {
    if (!simulationId) return;
    setLoading(true); setError("");
    try {
      const data = (await apiClient.get(`/api/intelligence/simulations/${simulationId}/adaptive/next`)).data;
      setSituation(data.situation);
      setResult(null);
      setResponse("");
      setSelectedChoice("");
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [simulationId]);

  useEffect(() => { loadNext(); }, [loadNext]);

  const submit = async (e, choice = null) => {
    e?.preventDefault();
    const submittedResponse = choice || response.trim();
    if (!submittedResponse) return;
    if (choice) setSelectedChoice(choice);
    setLoading(true); setError("");
    try {
      const data = (await apiClient.post(`/api/intelligence/simulations/${simulationId}/adaptive/respond`, {
        response: submittedResponse,
        decision: choice || "free_response",
      })).data;
      setResult({ ...data.evaluation, selected_option: choice });
      setSituation(data.next_situation);
      setResponse("");
      setSelectedChoice("");
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="card min-h-[440px] flex flex-col">
      <div className="flex items-center justify-between border-b border-border pb-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.18em] text-status-todo">Adaptive AI</p>
          <h3 className="font-display text-sm font-semibold">Decision Simulation</h3>
        </div>
        <button className="btn-ghost" onClick={loadNext} disabled={loading}>new situation</button>
      </div>
      <div className="pt-3 space-y-3">
        {error && <div className="rounded border border-status-blocked/40 bg-status-blocked/10 p-2 text-xs text-status-blocked">{error}</div>}
        {situation ? (
          <>
            <div className="rounded-md bg-surface-2 p-3">
              <p className="text-xs uppercase tracking-wide text-text-faint">{situation.title}</p>
              <p className="mt-2 text-base leading-7 text-text">{situation.situation}</p>
              <p className="mt-2 text-xs text-text-muted">Objective: {situation.objective}</p>
            </div>
            {situation.decision_options?.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-text-faint">Choose your response</p>
                {situation.decision_options.map((x) => (
                  <button
                    key={x}
                    type="button"
                    onClick={() => submit(null, x)}
                    disabled={loading}
                    aria-pressed={selectedChoice === x}
                    className={`w-full rounded-md border px-3 py-3 text-left text-sm leading-5 transition-colors focus:outline-none focus:ring-2 focus:ring-status-todo disabled:cursor-wait ${selectedChoice === x ? "border-status-todo bg-status-todo/15 text-text" : "border-border bg-surface-2 text-text-muted hover:border-status-todo hover:text-text"}`}
                  >
                    {x}
                  </button>
                ))}
              </div>
            )}
            {result && (
              <div className="rounded-md border border-status-completed/30 bg-status-completed/5 p-3">
                <div className="flex items-center justify-between"><span className="text-xs text-text-muted">AI evaluation</span><strong className="font-mono text-lg">{Math.round(result.overall_score)}/100</strong></div>
                {result.selected_option && <p className="mt-2 text-xs text-status-completed">Selected: {result.selected_option}</p>}
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
