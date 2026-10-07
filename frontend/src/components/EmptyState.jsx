import { motion } from "framer-motion";

/**
 * Consistent, polished empty state used across pages (timeline, meetings,
 * chat, board columns, admin, events). Logic-free: just icon/title/hint,
 * optional action. Keeps every "nothing here yet" moment on-brand.
 */
export default function EmptyState({ icon, title, hint, action, compact = false }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className={`flex flex-col items-center justify-center text-center ${compact ? "px-3 py-6" : "px-6 py-10"}`}
    >
      <div
        className={`relative mb-4 flex items-center justify-center rounded-2xl border border-white/10 bg-gradient-to-br from-brand-500/15 via-surface-2 to-accent-cyan/10 shadow-[0_0_40px_-12px_rgba(124,108,246,0.5)] ${compact ? "h-12 w-12" : "h-16 w-16"}`}
      >
        <span className={compact ? "text-xl" : "text-2xl"}>{icon || "✦"}</span>
        <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-accent-cyan shadow-[0_0_12px_2px_rgba(70,216,255,0.7)] animate-pulse-soft" />
      </div>
      <p className={`font-display font-semibold text-text ${compact ? "text-xs" : "text-sm"}`}>{title}</p>
      {hint && <p className={`mt-1.5 max-w-xs text-text-faint ${compact ? "text-[11px]" : "text-xs"}`}>{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </motion.div>
  );
}
