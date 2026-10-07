import { useState } from "react";
import { motion } from "framer-motion";

export default function EventResponseModal({ event, onClose, onRespond, isResponding }) {
  const [response, setResponse] = useState("");

  if (!event) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (response.trim()) {
      onRespond(event.id, response.trim());
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <motion.div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        onClick={onClose}
      />
      <motion.div
        initial={{ opacity: 0, scale: 0.94, y: 24 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
        className="card relative z-10 flex max-h-[90vh] w-full max-w-md flex-col overflow-hidden border-brand-400/25! shadow-[0_40px_120px_-20px_rgba(0,0,0,0.9),0_0_60px_-20px_rgba(124,108,246,0.6)]"
      >
        <div className="pointer-events-none absolute inset-x-0 top-0 h-16 bg-gradient-to-b from-brand-500/15 to-transparent" />
        <div className="relative flex items-center justify-between gap-3 border-b border-white/[0.06] p-4">
          <div>
            <p className="eyebrow">⚡ Workplace event</p>
            <h2 className="font-display text-lg font-bold">
              Respond to Event
            </h2>
          </div>
          <button onClick={onClose} className="btn-ghost">✕ Close</button>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Event Info */}
          <div className="p-3 bg-surface-2 rounded border border-border">
            <div className="flex items-center gap-2 mb-2">
              <span className="px-2 py-0.5 rounded text-xs font-mono uppercase tracking-wide bg-status-warning/20 text-status-warning">
                {event.event_type?.replace(/_/g, " ") || "Event"}
              </span>
            </div>
            <p className="text-sm text-text-muted">{event.message}</p>
            
            {event.original_requirement && (
              <div className="mt-3 space-y-3">
                <div>
                  <p className="text-xs text-text-faint mb-1">Original Requirement</p>
                  <p className="text-sm text-text-muted line-through bg-surface p-2 rounded">{event.original_requirement}</p>
                </div>
                <div>
                  <p className="text-xs text-text-faint mb-1">Updated Requirement</p>
                  <p className="text-sm text-text font-medium bg-status-todo/10 p-2 rounded border border-status-todo/20">{event.updated_requirement}</p>
                </div>
              </div>
            )}
          </div>

          {/* Response Input */}
          <div>
            <label className="block text-sm font-medium text-text-muted mb-2">
              Your Response
            </label>
            <textarea
              className="input w-full min-h-[120px]"
              placeholder="Acknowledge the requirement change and describe how you'll adapt your work..."
              value={response}
              onChange={(e) => setResponse(e.target.value)}
              required
              autoFocus
            />
            <p className="text-xs text-text-faint mt-1">
              Your response will be recorded and included in the evaluation evidence.
            </p>
          </div>

          {/* Buttons */}
          <div className="pt-4 border-t border-border flex gap-3 justify-end">
            <button
              type="button"
              onClick={onClose}
              className="btn-ghost"
              disabled={isResponding}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={isResponding || !response.trim()}
            >
              {isResponding ? "Recording..." : "Submit Response"}
            </button>
          </div>
        </form>
      </motion.div>
    </div>
  );
}