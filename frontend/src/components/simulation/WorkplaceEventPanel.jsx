import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import EventResponseModal from "./EventResponseModal";

export default function WorkplaceEventPanel({ events, activeEvent, onRespond, isResponding, suppressModal = false }) {
  const [respondingToEvent, setRespondingToEvent] = useState(null);
  useEffect(() => {
    if (suppressModal) setRespondingToEvent(null);
  }, [suppressModal]);
  if (!events || events.length === 0) {
    return (
      <div className="card flex h-full flex-col overflow-hidden">
        <div className="border-b border-white/[0.06] bg-white/[0.02] p-3.5">
          <h3 className="font-display text-xs font-bold uppercase tracking-[0.14em] text-text-faint">
            ⚡ Workplace Events
          </h3>
        </div>
        <div className="flex flex-1 items-center justify-center">
          <div className="p-4 text-center text-text-muted">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-white/10 bg-gradient-to-br from-brand-500/15 to-accent-cyan/10 text-xl shadow-[0_0_30px_-10px_rgba(124,108,246,0.6)]">
              ⚡
            </div>
            <p className="text-sm font-semibold text-text">All quiet on the floor</p>
            <p className="mt-1 text-xs text-text-faint">Workplace events will appear here as you work on tasks.</p>
          </div>
        </div>
      </div>
    );
  }

  const eventTypeIcons = {
    requirement_change: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
      </svg>
    ),
    deadline_warning: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
    stakeholder_message: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h4M8 12l4 4m0-4l4-4M8 12v10a2 2 0 002 2h6a2 2 0 002-2v-4M8 12l4-4m0 0l4 4m-4-4v10" />
      </svg>
    ),
    bug_report: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
    priority_change: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
      </svg>
    ),
  };

  const eventTypeColors = {
    requirement_change: "border-status-warning text-status-warning bg-status-warning/10",
    deadline_warning: "border-status-progress text-status-progress bg-status-progress/10",
    stakeholder_message: "border-status-todo text-status-todo bg-status-todo/10",
    bug_report: "border-status-progress text-status-progress bg-status-progress/10",
    priority_change: "border-status-review text-status-review bg-status-review/10",
  };

  const eventTypeLabels = {
    requirement_change: "Requirement Change",
    deadline_warning: "Deadline Warning",
    stakeholder_message: "Stakeholder Message",
    bug_report: "Bug Report",
    priority_change: "Priority Change",
  };

  return (
    <div className="card flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.06] bg-white/[0.02] p-3.5">
        <h3 className="font-display text-xs font-bold uppercase tracking-[0.14em] text-text-faint">
          ⚡ Workplace Events
        </h3>
        <span className="chip chip-glow font-mono text-[10px]!">
          {events.length} live
        </span>
      </div>

      {/* Events List */}
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {events.map((event) => (
          <motion.div
            key={event.id}
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ type: "spring", stiffness: 300, damping: 28 }}
            className={`rounded-xl border p-3 backdrop-blur-sm transition-all duration-200 ${
              event.id === activeEvent?.id
                ? "border-brand-400/60 shadow-[0_0_32px_-10px_rgba(124,108,246,0.9)]"
                : "hover:-translate-y-px"
            } ${eventTypeColors[event.event_type] || "border-white/[0.07] bg-white/[0.03]"}`}
          >
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0"
                style={{ backgroundColor: `var(--color-${eventTypeColors[event.event_type]?.split(" ")[0]?.replace("border-", "") || "status-todo"})/20` }}>
                {eventTypeIcons[event.event_type] || (
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                )}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">{eventTypeLabels[event.event_type] || event.event_type}</span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wide ${event.status === "pending" ? "bg-status-warning/20 text-status-warning" : "bg-status-completed/20 text-status-completed"}`}>
                    {event.status}
                  </span>
                </div>
                <p className="mt-1 text-sm text-text-muted">{event.message}</p>
                <p className="mt-1 text-xs text-text-faint">
                  {new Date(event.occurred_at).toLocaleTimeString()}
                </p>

                {/* Requirement Change Details */}
                {event.event_type === "requirement_change" && (
                  <div className="mt-2 space-y-2">
                    {event.original_requirement && (
                      <div className="p-2 bg-surface-2 rounded border-l-4 border-status-blocked">
                        <p className="text-xs text-text-faint mb-1">Original Requirement</p>
                        <p className="text-sm text-text-muted line-through">{event.original_requirement}</p>
                      </div>
                    )}
                    {event.updated_requirement && (
                      <div className="p-2 bg-surface-2 rounded border-l-4 border-status-todo">
                        <p className="text-xs text-text-faint mb-1">Updated Requirement</p>
                        <p className="text-sm text-text font-medium">{event.updated_requirement}</p>
                      </div>
                    )}
                  </div>
                )}

                {/* Response Button */}
                {event.action_required && event.status === "pending" && (
                  <button
                    onClick={() => setRespondingToEvent(event)}
                    className="mt-2 btn-primary text-sm py-1.5"
                    disabled={isResponding}
                  >
                    {isResponding ? "Responding..." : "Respond"}
                  </button>
                )}

                {/* Response Display */}
                {event.response && (
                  <div className="mt-2 p-2 bg-surface-2 rounded border border-border">
                    <p className="text-xs text-text-faint mb-1">Your Response</p>
                    <p className="text-sm text-text">{event.response}</p>
                    <p className="text-xs text-text-faint mt-1">
                      Responded at {new Date(event.responded_at).toLocaleTimeString()}
                    </p>
                  </div>
                )}
              </div>
            </div>
          </motion.div>
        ))}
      </div>

      {/* Event Response Modal */}
      {!suppressModal && respondingToEvent && respondingToEvent.action_required && respondingToEvent.status === "pending" && (
        <EventResponseModal
          event={respondingToEvent}
          onClose={() => setRespondingToEvent(null)}
          onRespond={(eventId, response) => {
            onRespond(eventId, response);
            setRespondingToEvent(null);
          }}
          isResponding={isResponding}
        />
      )}
    </div>
  );
}
