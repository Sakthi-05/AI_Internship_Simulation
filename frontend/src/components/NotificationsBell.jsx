import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNotifications } from "../hooks/useApi";

export default function NotificationsBell({ companyId }) {
  const [open, setOpen] = useState(false);
  const { data: notifications, markRead, isLoading, error, refetch } = useNotifications(companyId);
  const unread = (notifications || []).filter((n) => !n.is_read);

  return (
    <div className="relative">
      <motion.button
        whileHover={{ y: -1 }}
        whileTap={{ scale: 0.96 }}
        onClick={() => setOpen((v) => !v)}
        className="relative rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1 text-text-muted transition-all duration-200 hover:border-brand-400/50 hover:text-text hover:shadow-[0_0_20px_-6px_rgba(124,108,246,0.8)]"
      >
        <span className="font-mono text-xs">🔔 notifications</span>
        {unread.length > 0 && (
          <span className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 animate-pulse-soft items-center justify-center rounded-full bg-gradient-to-br from-status-blocked to-accent-pink px-1 text-[10px] font-bold text-white shadow-[0_0_12px_rgba(239,91,91,0.9)]">
            {unread.length}
          </span>
        )}
      </motion.button>

      <AnimatePresence>
        {open && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
            <motion.div
              initial={{ opacity: 0, y: -8, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.98 }}
              transition={{ type: "spring", stiffness: 400, damping: 30 }}
              className="popover absolute right-0 top-full z-50 mt-2 max-h-96 w-80 overflow-y-auto p-2"
            >
              <p className="eyebrow px-2 pb-1 pt-2">Inbox</p>
              {isLoading && <p className="px-2 py-3 text-xs text-text-faint">Loading notifications…</p>}
              {error && <div className="m-1 rounded-lg border border-status-blocked/30 p-2 text-xs text-status-blocked" role="alert">Could not load notifications: {error.message}<button className="btn-ghost mt-2 w-full" onClick={() => refetch()}>Retry</button></div>}
              {markRead.error && <p className="m-1 rounded-lg border border-status-blocked/30 p-2 text-xs text-status-blocked" role="alert">Notification update failed: {markRead.error.message}</p>}
              {!isLoading && !error && (notifications || []).length === 0 && (
                <div className="flex flex-col items-center px-3 py-6 text-center">
                  <span className="mb-2 text-2xl opacity-60">📭</span>
                  <p className="text-xs font-medium text-text-muted">All caught up</p>
                  <p className="mt-0.5 text-[11px] text-text-faint">No notifications yet.</p>
                </div>
              )}
              {(notifications || []).map((n) => (
                <motion.button
                  key={n.id}
                  whileHover={{ x: 2 }}
                  onClick={() => !n.is_read && markRead.mutate(n.id)}
                  className={`mb-1 block w-full rounded-xl border p-2.5 text-left text-xs transition-all ${
                    n.is_read
                      ? "border-transparent text-text-faint"
                      : "border-brand-400/25 bg-gradient-to-r from-brand-500/[0.12] to-transparent text-text shadow-[0_0_20px_-10px_rgba(124,108,246,0.7)]"
                  }`}
                >
                  <p className="flex items-center gap-1.5 font-semibold">
                    {!n.is_read && <span className="h-1.5 w-1.5 rounded-full bg-brand-300 shadow-[0_0_8px_rgba(139,124,246,1)]" />}
                    {n.notification_type.replace(/_/g, " ")}
                  </p>
                  <p className="mt-0.5 leading-relaxed text-text-muted">{n.message}</p>
                  {n.created_at && <p className="mt-1 text-[10px] text-text-faint">{new Date(n.created_at).toLocaleString()}</p>}
                </motion.button>
              ))}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
