import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
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
    <div className="card flex min-h-[440px] flex-col overflow-hidden p-4">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-16 bg-gradient-to-b from-brand-500/10 to-transparent" />
      <div className="relative flex items-center justify-between gap-2 border-b border-white/[0.06] pb-3">
        <div>
          <p className="eyebrow">✦ Adaptive AI</p>
          <h3 className="font-display text-sm font-bold">Decision Simulation</h3>
        </div>
        <button className="btn-ghost" onClick={loadNext} disabled={loading}>↻ new situation</button>
      </div>
      <div className="relative space-y-3 pt-3">
        {error && <div className="rounded-xl border border-status-blocked/40 bg-status-blocked/10 p-2.5 text-xs text-status-blocked">{error}</div>}
        {situation ? (
          <>
            <div className="rounded-2xl border border-brand-400/20 bg-gradient-to-br from-brand-500/[0.08] to-transparent p-3.5">
              <p className="text-[11px] font-bold uppercase tracking-wider text-brand-300">{situation.title}</p>
              <p className="mt-2 text-[15px] leading-7 text-text">{situation.situation}</p>
              <p className="mt-2 text-xs text-text-muted"><span className="text-text-faint">Objective:</span> {situation.objective}</p>
            </div>
            {situation.decision_options?.length > 0 && (
              <div className="space-y-2">
                <p className="text-[11px] font-bold uppercase tracking-wider text-text-faint">Choose your response</p>
                {situation.decision_options.map((x, i) => (
                  <motion.button
                    key={x}
                    type="button"
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.05 }}
                    whileHover={{ x: 3 }}
                    whileTap={{ scale: 0.985 }}
                    onClick={() => submit(null, x)}
                    disabled={loading}
                    aria-pressed={selectedChoice === x}
                    className={`w-full rounded-xl border px-3.5 py-3 text-left text-sm leading-5 transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-brand-400 disabled:cursor-wait ${selectedChoice === x ? "border-brand-400/60 bg-brand-500/15 text-text shadow-[0_0_24px_-8px_rgba(124,108,246,0.8)]" : "border-white/[0.07] bg-white/[0.02] text-text-muted hover:border-brand-400/35 hover:bg-brand-500/[0.06] hover:text-text"}`}
                  >
                    <span className="mr-2 font-mono text-brand-300">{String.fromCharCode(65 + i)}</span>{x}
                  </motion.button>
                ))}
              </div>
            )}
            {result && (
              <motion.div
                initial={{ opacity: 0, scale: 0.97 }}
                animate={{ opacity: 1, scale: 1 }}
                className="rounded-2xl border border-status-completed/30 bg-status-completed/[0.06] p-3.5 shadow-[0_0_32px_-12px_rgba(62,214,149,0.6)]"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-text-muted">✦ AI evaluation</span>
                  <strong className="gradient-text font-mono text-xl">{Math.round(result.overall_score)}<span className="text-xs text-text-faint">/100</span></strong>
                </div>
                <div className="progress-track mt-2 h-1.5">
                  <motion.div
                    className="h-full rounded-full bg-gradient-to-r from-status-completed to-accent-cyan"
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.round(result.overall_score)}%` }}
                    transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
                  />
                </div>
                {result.selected_option && <p className="mt-2 text-xs text-status-completed">Selected: {result.selected_option}</p>}
                <p className="mt-2 text-xs leading-5 text-text-muted">{result.feedback}</p>
                <p className="mt-2 font-mono text-[11px] text-brand-300">Next branch → {result.branch_selected}</p>
              </motion.div>
            )}
            <form onSubmit={submit} className="space-y-2">
              <textarea className="input min-h-24 resize-none" value={response} onChange={(e) => setResponse(e.target.value)} placeholder="Explain what you would do and why..." />
              <button className="btn-primary w-full py-2.5" disabled={loading || !response.trim()}>
                {loading ? (
                  <span className="flex items-center gap-2">
                    <span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin" style={{ animationDuration: "0.7s" }} />
                    AI is evaluating...
                  </span>
                ) : "Submit decision →"}
              </button>
            </form>
          </>
        ) : (
          <div className="flex flex-col items-center py-8 text-center">
            {loading ? (
              <>
                <div className="relative h-10 w-10">
                  <div className="absolute inset-0 rounded-full border-2 border-brand-400/20" />
                  <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-brand-300 animate-spin" style={{ animationDuration: "0.9s" }} />
                </div>
                <p className="mt-3 text-xs text-text-muted">Generating adaptive situation...</p>
              </>
            ) : (
              <p className="text-xs text-text-faint">No situation available.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
