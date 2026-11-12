import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useManager, useTeam, useManagerChat, useTeamChat } from "../../hooks/useApi";

export default function SimulationChatPanel({ companyId }) {
  const [activeThread, setActiveThread] = useState("manager");
  const [collapsed, setCollapsed] = useState(false);

  const { data: manager } = useManager(companyId);
  const { data: team } = useTeam(companyId);

  const threads = [
    { id: "manager", label: manager?.name || "Manager", sub: manager?.title, icon: "M" },
    ...(team || []).map((t, i) => ({ id: t.id, label: t.name, sub: t.role, icon: t.name?.[0] || `T${i + 1}` })),
  ];

  if (collapsed) {
    return (
      <div className="card p-2">
        <button
          onClick={() => setCollapsed(false)}
          className="flex w-full items-center gap-2 rounded-xl px-2 py-1.5 text-sm transition-all hover:bg-brand-500/10"
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-accent-cyan text-xs shadow-[0_0_16px_-4px_rgba(124,108,246,0.9)]">
            💬
          </span>
          <span className="font-medium text-text-muted">Team chat</span>
          <span className="ml-auto flex h-5 w-5 items-center justify-center rounded-full border border-brand-400/40 text-xs text-brand-300">+</span>
        </button>
      </div>
    );
  }

  return (
    <div className="card flex flex-col overflow-hidden" style={{ height: "100%" }}>
      <div className="flex items-center justify-between border-b border-white/[0.06] bg-gradient-to-r from-brand-500/[0.1] to-transparent px-3 py-2">
        <span className="flex items-center gap-1.5 text-xs font-bold">
          <span className="h-1.5 w-1.5 rounded-full bg-status-completed shadow-[0_0_8px_rgba(62,214,149,0.9)] animate-pulse-soft" />
          Team Chat
        </span>
        <button
          onClick={() => setCollapsed(true)}
          className="flex h-5 w-5 items-center justify-center rounded-md text-text-faint transition-colors hover:bg-white/10 hover:text-text"
        >
          <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>

      <div className="flex border-b border-white/[0.05] bg-ink/30">
        {threads.slice(0, 4).map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveThread(t.id)}
            className={`relative flex-1 px-2 py-2 text-[10px] font-semibold transition-all duration-200 ${
              activeThread === t.id
                ? "text-brand-200"
                : "text-text-faint hover:text-text-muted"
            }`}
          >
            <span className="block truncate">{t.label}</span>
            {activeThread === t.id && (
              <motion.span
                layoutId={`sim-thread-${companyId}`}
                className="absolute inset-x-2 bottom-0 h-[2px] rounded-full bg-gradient-to-r from-brand-400 to-accent-cyan shadow-[0_0_10px_2px_rgba(124,108,246,0.7)]"
              />
            )}
          </button>
        ))}
      </div>

      <div className="flex-1 min-h-0">
        <ChatThread
          kind={activeThread === "manager" ? "manager" : "team"}
          companyId={companyId}
          teammateId={activeThread !== "manager" ? activeThread : undefined}
          managerName={manager?.name}
          team={team}
          activeLabel={threads.find((t) => t.id === activeThread)?.label}
        />
      </div>
    </div>
  );
}

function formatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function ChatThread({ kind, companyId, teammateId, managerName, team, activeLabel }) {
  const managerChat = useManagerChat(kind === "manager" ? companyId : undefined);
  const teamChat = useTeamChat(kind === "team" ? companyId : undefined, kind === "team" ? teammateId : undefined);
  const { history, send } = kind === "manager" ? managerChat : teamChat;
  const messagesRef = useRef(null);
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState("");
  const pendingRequest = useRef(null);

  const messages = history.data || [];

  const nameFor = (m) => {
    if (m.sender_type === "student") return "You";
    if (m.sender_type === "manager") return managerName || "Manager";
    return (team || []).find((t) => t.id === m.sender_id)?.name || activeLabel || "Teammate";
  };

  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    }
  }, [messages.length]);

  const handleSend = async (e) => {
    e.preventDefault();
    if (!draft.trim()) return;
    const text = draft;
    setDraft("");
    try {
      if (pendingRequest.current?.content !== text) {
        pendingRequest.current = { content: text, key: crypto.randomUUID() };
      }
      await send.mutateAsync({ content: text, idempotency_key: pendingRequest.current.key });
      pendingRequest.current = null;
      setSendError("");
    } catch (error) {
      setDraft(text);
      setSendError(error.message || "Message could not be sent.");
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div ref={messagesRef} className="flex-1 space-y-2.5 overflow-y-auto p-2.5">
        {history.isLoading && <p className="text-xs text-text-faint" role="status">Loading conversation…</p>}
        {history.error && <div className="rounded-lg border border-status-blocked/30 p-2 text-xs text-status-blocked" role="alert">Conversation could not be loaded: {history.error.message}<button className="btn-ghost ml-2" onClick={() => history.refetch()}>Retry</button></div>}
        {sendError && <p className="rounded-lg border border-status-blocked/30 p-2 text-xs text-status-blocked" role="alert">{sendError}</p>}
        <AnimatePresence initial={false}>
          {messages.map((m) => {
            const mine = m.sender_type === "student";
            return (
              <motion.div
                key={m.id}
                initial={{ opacity: 0, y: 8, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
                className={`flex ${mine ? "justify-end" : "justify-start"}`}
              >
                <div className={`max-w-[88%] ${mine ? "text-right" : "text-left"}`}>
                  <p className="mb-0.5 text-[10px] font-medium text-text-faint">
                    {nameFor(m)}{m.created_at ? ` · ${formatTime(m.created_at)}` : ""}
                  </p>
                  <div
                    className={`px-2.5 py-1.5 text-xs leading-relaxed ${
                      mine
                        ? "bubble-me"
                        : "bubble-them"
                    }`}
                  >
                    {m.content}
                  </div>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
        {messages.length === 0 && !history.isLoading && !history.error && (
          <div className="flex flex-col items-center px-2 py-6 text-center">
            <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-2xl border border-white/10 bg-gradient-to-br from-brand-500/15 to-accent-cyan/10 text-base">
              💬
            </div>
            <p className="text-[11px] font-medium text-text-muted">Quiet channel… for now</p>
            <p className="mt-0.5 text-[10px] text-text-faint">The team posts updates on its own</p>
          </div>
        )}
      </div>

      <form onSubmit={handleSend} className="flex gap-1.5 border-t border-white/[0.06] bg-ink/40 p-2">
        <input
          className="input rounded-full! border-white/10! bg-white/[0.04]! px-3! py-1.5! text-xs!"
          placeholder="Type..."
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={send.isPending}
        />
        <motion.button
          whileTap={{ scale: 0.92 }}
          className="shrink-0 rounded-full bg-gradient-to-r from-brand-500 to-brand-400 px-3 py-1.5 text-xs font-bold text-white shadow-[0_6px_18px_-6px_rgba(124,108,246,0.9)] transition-all hover:brightness-110 disabled:opacity-50"
          disabled={send.isPending || !draft.trim()}
        >
          {send.isPending ? "…" : "↑"}
        </motion.button>
      </form>
    </div>
  );
}
