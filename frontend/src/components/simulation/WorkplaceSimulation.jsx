import { useState } from "react";
import { motion } from "framer-motion";
import {
  activeRequirementChange,
  popupRequired,
  workplacePhase,
} from "./workplaceScenario";

export default function WorkplaceSimulation({
  events,
  isLoading = false,
  error,
  onRetry,
  onAcknowledge,
  onReply,
  onRecordAction,
  isAcknowledging = false,
  isReplying = false,
  isRecording = false,
}) {
  const [reply, setReply] = useState("");
  const [adaptation, setAdaptation] = useState("");
  const [eventResponses, setEventResponses] = useState({});
  const change = activeRequirementChange(events);
  const phase = workplacePhase(change);
  const otherEvents = (events || []).filter((event) => event.id !== change?.id);

  return (
    <div className="workplace-simulation-card card flex min-h-[300px] flex-col">
      <div className="workplace-simulation-header border-b p-4">
        <p className="eyebrow">Workplace context</p>
        <h3 className="mt-1 font-display text-sm font-bold">Manager updates</h3>
      </div>
      <div className="space-y-3 p-4">
        {isLoading && <p className="text-xs text-text-faint" role="status">Loading workplace events…</p>}
        {error && <div className="rounded-lg border border-status-blocked/30 p-3 text-xs text-status-blocked" role="alert">Workplace events could not be loaded: {error.message}<button className="btn-ghost mt-2 w-full" onClick={onRetry}>Retry</button></div>}
        {!change && !isLoading && !error && (
          <p className="text-sm text-text-muted">
            Workplace events and your responses are saved to simulation history for later review.
          </p>
        )}
        {change && phase !== "acknowledge" && (
          <section className="workplace-update-card space-y-3 rounded-xl border p-3.5">
            <p className="text-sm text-text">{change.message}</p>
            {change.original_requirement && (
              <div>
                <p className="text-xs text-text-faint">Previous criteria</p>
                <p className="text-sm text-text-muted line-through whitespace-pre-wrap">{change.original_requirement}</p>
              </div>
            )}
            {change.updated_requirement && (
              <div>
                <p className="text-xs text-text-faint">New requirement</p>
                <p className="text-sm font-medium text-text">{change.updated_requirement}</p>
              </div>
            )}
            {change.manager_message && (
              <div className="manager-message-box rounded-lg border p-2.5">
                <p className="text-xs text-brand-300">Manager</p>
                <p className="text-sm text-text">{change.manager_message}</p>
              </div>
            )}
            {change.response && (
              <div>
                <p className="text-xs text-text-faint">Your reply</p>
                <p className="text-sm text-text">{change.response}</p>
              </div>
            )}
            {(change.subsequent_actions || []).filter((item) => item.action === "adapted_work").map((item, index) => (
              <div key={`${item.at || index}-${index}`}>
                <p className="text-xs text-text-faint">Adaptation you recorded</p>
                <p className="text-sm text-text">{item.detail}</p>
                {item.criteria_version_number != null && (
                  <p className="mt-1 text-[11px] text-text-faint">Saved for criteria version {item.criteria_version_number}</p>
                )}
              </div>
            ))}
          </section>
        )}
        {phase === "reply" && (
          <form
            className="space-y-2"
            onSubmit={async (event) => {
              event.preventDefault();
              if (!reply.trim()) return;
              if (await onReply(change.id, reply.trim())) setReply("");
            }}
          >
            <label className="block text-sm text-text-muted" htmlFor="requirement-reply">Reply to your manager</label>
            <textarea
              id="requirement-reply"
              className="input min-h-24 w-full"
              value={reply}
              onChange={(event) => setReply(event.target.value)}
              placeholder="Explain how you will adapt the work already started."
              required
            />
            <button className="btn-primary w-full" type="submit" disabled={isReplying || !reply.trim()}>
              {isReplying ? "Sending reply..." : "Send reply"}
            </button>
          </form>
        )}
        {phase === "adapt" && (
          <form
            className="space-y-2"
          onSubmit={async (event) => {
              event.preventDefault();
              if (!adaptation.trim()) return;
            if (await onRecordAction(change.id, adaptation.trim())) setAdaptation("");
            }}
          >
            <label className="block text-sm text-text-muted" htmlFor="requirement-adaptation">What did you change in the task?</label>
            <textarea
              id="requirement-adaptation"
              className="input min-h-24 w-full"
              value={adaptation}
              onChange={(event) => setAdaptation(event.target.value)}
              placeholder="Describe the work you updated for the new requirement."
              required
            />
            <button className="btn-primary w-full" type="submit" disabled={isRecording || !adaptation.trim()}>
              {isRecording ? "Saving..." : "Record adaptation"}
            </button>
          </form>
        )}
        {phase === "verify" && (
          <p className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3 text-xs text-text-muted">
            Your adaptation is saved. Submit task work that meets criteria version {change.criteria_version_number ?? "—"} to provide verifiable evidence.
          </p>
        )}
        {otherEvents.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-text-faint">Other workplace updates</p>
            {otherEvents.map((event) => (
              <div key={event.id} className="workplace-other-event rounded-lg border p-2.5">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm text-text">{event.title || event.event_type}</p>
                  <span className="text-[10px] capitalize text-text-faint">{event.status}</span>
                </div>
                {event.occurred_at && <p className="mt-1 text-[10px] text-text-faint">{new Date(event.occurred_at).toLocaleString()}</p>}
                <p className="text-xs text-text-muted">{event.message}</p>
                {event.description && event.description !== event.message && <p className="mt-1 whitespace-pre-wrap text-xs text-text-faint">{event.description}</p>}
                {event.response && (
                  <div className="mt-2 border-t border-white/[0.06] pt-2">
                    <p className="text-[11px] text-text-faint">Your response</p>
                    <p className="whitespace-pre-wrap text-xs text-text-muted">{event.response}</p>
                  </div>
                )}
                {!event.response && event.status === "pending" && (
                  <form className="mt-2 space-y-2" onSubmit={async (e) => {
                    e.preventDefault();
                    const value = (eventResponses[event.id] || "").trim();
                    if (!value) return;
                    if (await onReply(event.id, value)) setEventResponses((current) => ({ ...current, [event.id]: "" }));
                  }}>
                    <label className="sr-only" htmlFor={`workplace-event-${event.id}`}>Respond to {event.title || "workplace event"}</label>
                    <textarea id={`workplace-event-${event.id}`} className="input min-h-16 w-full" value={eventResponses[event.id] || ""} onChange={(e) => setEventResponses((current) => ({ ...current, [event.id]: e.target.value }))} placeholder="Ask a question, explain your approach, or record a decision." />
                    <button className="btn-ghost w-full" type="submit" disabled={isReplying || !eventResponses[event.id]?.trim()}>{isReplying ? "Saving…" : "Record response"}</button>
                  </form>
                )}
                {(event.subsequent_actions || []).filter((item) => item.action === "adapted_work").map((item, index) => (
                  <div key={`${item.at || index}-${index}`} className="mt-2 border-t border-white/[0.06] pt-2">
                    <p className="text-[11px] text-text-faint">Adaptation saved for criteria v{item.criteria_version_number ?? "?"}</p>
                    <p className="text-xs text-text-muted">{item.detail}</p>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
      {popupRequired(change) && (
        <RequirementPopup event={change} onAcknowledge={onAcknowledge} isAcknowledging={isAcknowledging} />
      )}
    </div>
  );
}

function RequirementPopup({ event, onAcknowledge, isAcknowledging }) {
  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="requirement-change-title">
      <div className="fixed inset-0 bg-black/70" />
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className="card relative z-10 w-full max-w-lg space-y-4 p-5"
      >
        <div>
          <p className="eyebrow">Requirement change</p>
          <h2 id="requirement-change-title" className="font-display text-lg font-bold">The task requirements changed</h2>
        </div>
        <p className="text-sm text-text-muted">{event.message}</p>
        {event.original_requirement && (
          <div className="rounded-lg border border-white/[0.06] p-3">
            <p className="text-xs text-text-faint">Previous criteria</p>
            <p className="mt-1 whitespace-pre-wrap text-sm text-text-muted line-through">{event.original_requirement}</p>
          </div>
        )}
        <div className="rounded-lg border border-status-warning/30 bg-status-warning/10 p-3">
          <p className="text-xs text-status-warning">New requirement</p>
          <p className="mt-1 text-sm font-medium text-text">{event.updated_requirement}</p>
        </div>
        <button className="btn-primary w-full" type="button" onClick={() => onAcknowledge(event.id)} disabled={isAcknowledging}>
          {isAcknowledging ? "Acknowledging..." : "Acknowledge change"}
        </button>
      </motion.div>
    </div>
  );
}
