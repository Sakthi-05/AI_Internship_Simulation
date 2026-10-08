import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useSimulation } from "../context/SimulationContext";
import { useManager, useTeam, useManagerChat, useTeamChat } from "../hooks/useApi";

export function formatChatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export default function Chat() {
  const { simulation } = useSimulation();

  const { data: manager } = useManager(simulation?.companyId);
  const { data: team } = useTeam(simulation?.companyId);

  const [activeThread, setActiveThread] = useState("manager");

  const threads = [
    { id: "manager", label: manager?.name || "Manager", sub: manager?.title },
    ...(team || []).map((t) => ({ id: t.id, label: t.name, sub: t.role })),
  ];

  return (
    <div className="flex h-full gap-3 p-3">
      <aside className="card flex w-60 shrink-0 flex-col gap-1 overflow-y-auto p-2">
        <p className="eyebrow px-2 pb-1 pt-2">Workplace channels</p>
        {threads.map((t, i) => {
          const active = activeThread === t.id;
          const isManager = t.id === "manager";
          return (
            <motion.button
              key={t.id}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.05 }}
              onClick={() => setActiveThread(t.id)}
              className={`relative flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2.5 text-left text-sm transition-all duration-200 ${
                active
                  ? "border border-brand-400/40 bg-gradient-to-r from-brand-500/20 to-accent-cyan/10 text-text shadow-[0_0_28px_-10px_rgba(124,108,246,0.8)]"
                  : "border border-transparent text-text-muted hover:border-white/[0.06] hover:bg-white/[0.03] hover:text-text"
              }`}
            >
              {active && (
                <motion.span
                  layoutId="chat-thread-glow"
                  className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-gradient-to-b from-brand-300 to-accent-cyan shadow-[0_0_10px_2px_rgba(124,108,246,0.8)]"
                />
              )}
              <span
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl font-display text-sm font-bold text-white ${
                  isManager
                    ? "bg-gradient-to-br from-brand-500 to-accent-cyan shadow-[0_0_18px_-4px_rgba(124,108,246,0.9)]"
                    : "bg-gradient-to-br from-surface-3 to-brand-600 shadow-[0_0_14px_-6px_rgba(124,108,246,0.7)]"
                }`}
              >
                {t.label?.[0] || "?"}
              </span>
              <span className="min-w-0">
                <span className="block truncate font-medium leading-tight">{t.label}</span>
                {t.sub && <span className="block truncate text-xs text-text-faint">{t.sub}</span>}
              </span>
              <span className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-status-completed shadow-[0_0_8px_rgba(62,214,149,0.9)] animate-pulse-soft" />
            </motion.button>
          );
        })}
        <div className="mt-auto rounded-xl border border-white/[0.05] bg-ink/40 p-2.5 text-[11px] leading-relaxed text-text-faint">
          <span className="text-brand-300">✦ Live workplace</span>
          <br />Teammates post updates on their own — no prompt needed.
        </div>
      </aside>

      <div className="card min-w-0 flex-1 overflow-hidden">
        {activeThread === "manager" ? (
          <ChatThread
            key="manager"
            kind="manager"
            companyId={simulation?.companyId}
            title={manager?.name}
            nameFor={(m) =>
              m.sender_type === "student" ? "You" : manager?.name || "Manager"
            }
          />
        ) : (
          <ChatThread
            key={activeThread}
            kind="team"
            companyId={simulation?.companyId}
            teammateId={activeThread}
            title={threads.find((t) => t.id === activeThread)?.label}
            nameFor={(m) => {
              if (m.sender_type === "student") return "You";
              return (team || []).find((t) => t.id === m.sender_id)?.name
                || threads.find((t) => t.id === activeThread)?.label
                || "Teammate";
            }}
          />
        )}
      </div>
    </div>
  );
}

function ChatThread({ kind, companyId, teammateId, title, nameFor }) {
  const managerChat = useManagerChat(kind === "manager" ? companyId : undefined);
  const teamChat = useTeamChat(kind === "team" ? companyId : undefined, kind === "team" ? teammateId : undefined);
  const { history, send } = kind === "manager" ? managerChat : teamChat;

  const [draft, setDraft] = useState("");
  const scrollRef = useRef(null);

  const handleSend = async (e) => {
    e.preventDefault();
    if (!draft.trim()) return;
    const text = draft;
    setDraft("");
    await send.mutateAsync(text);
  };

  const messages = history.data || [];

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-white/[0.06] bg-white/[0.02] px-4 py-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-accent-cyan font-display text-base font-bold text-white shadow-[0_0_24px_-6px_rgba(124,108,246,0.9)]">
          {title?.[0] || "?"}
        </span>
        <div className="min-w-0">
          <h2 className="truncate font-display text-sm font-semibold">{title}</h2>
          <p className="flex items-center gap-1.5 text-xs text-text-faint">
            <span className="h-1.5 w-1.5 rounded-full bg-status-completed shadow-[0_0_8px_rgba(62,214,149,0.9)] animate-pulse-soft" />
            online · posts updates on its own
          </p>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-3.5 overflow-y-auto p-4">
        <AnimatePresence initial={false}>
          {messages.map((m) => {
            const mine = m.sender_type === "student";
            return (
              <motion.div
                key={m.id}
                initial={{ opacity: 0, y: 10, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ type: "spring", stiffness: 380, damping: 30 }}
                className={`flex ${mine ? "justify-end" : "justify-start"}`}
              >
                <div className={`max-w-md ${mine ? "text-right" : "text-left"}`}>
                  <p className="mb-1 text-[11px] font-medium text-text-faint">
                    {nameFor ? nameFor(m) : mine ? "You" : m.sender_type}
                    {m.created_at ? ` · ${formatChatTime(m.created_at)}` : ""}
                  </p>
                  <div className={mine ? "bubble-me" : "bubble-them"}>
                    {m.content}
                  </div>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
        {messages.length === 0 && (
          <div className="flex flex-col items-center py-10 text-center">
            <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-gradient-to-br from-brand-500/15 to-accent-cyan/10 text-2xl shadow-[0_0_36px_-10px_rgba(124,108,246,0.6)]">
              💬
            </div>
            <p className="text-sm font-medium text-text-muted">Quiet… for now</p>
            <p className="mt-1 max-w-xs text-xs text-text-faint">No messages yet — check back soon, the team posts updates on its own.</p>
          </div>
        )}
      </div>

      <form onSubmit={handleSend} className="flex gap-2 border-t border-white/[0.06] bg-ink/40 p-3">
        <input
          className="input rounded-full! px-4!"
          placeholder="Type a message..."
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={send.isPending}
        />
        <motion.button
          whileHover={{ scale: 1.04 }}
          whileTap={{ scale: 0.96 }}
          className="btn-primary rounded-full! px-5"
          disabled={send.isPending || !draft.trim()}
        >
          {send.isPending ? (
            <span className="flex items-center gap-1.5">
              <span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin" style={{ animationDuration: "0.7s" }} />
            </span>
          ) : (
            "Send ↑"
          )}
        </motion.button>
      </form>
    </div>
  );
}
