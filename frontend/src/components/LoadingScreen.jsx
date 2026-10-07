import { motion } from "framer-motion";

const LINES = [
  "resolving internship brief...",
  "provisioning company...",
  "assigning manager...",
  "scoping project...",
  "splitting into sprints...",
  "writing tickets...",
];

export default function LoadingScreen({ label = "Generating your internship simulation" }) {
  return (
    <div className="flex h-full min-h-[60vh] items-center justify-center p-6">
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="card w-full max-w-md p-6 font-mono text-sm"
      >
        <div className="mb-5 flex items-center gap-4">
          {/* Orbital AI loader */}
          <div className="relative h-12 w-12 shrink-0">
            <div
              className="absolute inset-0 rounded-full"
              style={{
                background: "conic-gradient(from 0deg, #7c6cf6, #46d8ff, #f471b5, #7c6cf6)",
                animation: "orb-spin 1.6s linear infinite",
                filter: "blur(1px)",
              }}
            />
            <div className="absolute inset-[3px] rounded-full bg-surface" />
            <div
              className="absolute inset-[10px] rounded-full bg-gradient-to-br from-brand-400 to-accent-cyan shadow-[0_0_18px_4px_rgba(124,108,246,0.7)]"
              style={{ animation: "orb-pulse 1.6s ease-in-out infinite" }}
            />
          </div>
          <div>
            <p className="eyebrow">AI engine working</p>
            <p className="mt-1 text-text">{label}</p>
          </div>
        </div>
        <div className="space-y-2">
          {LINES.map((line, i) => (
            <motion.div
              key={line}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.15 + i * 0.3, duration: 0.3 }}
              className="flex items-center gap-2.5 rounded-lg border border-white/[0.04] bg-white/[0.02] px-2.5 py-1.5"
            >
              <motion.span
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ delay: 0.3 + i * 0.3, type: "spring", stiffness: 400, damping: 15 }}
                className="flex h-4 w-4 items-center justify-center rounded-full bg-status-completed/15 text-[10px] text-status-completed shadow-[0_0_10px_rgba(62,214,149,0.5)]"
              >
                ✓
              </motion.span>
              <span className="text-text-muted">{line}</span>
            </motion.div>
          ))}
        </div>
        <div className="progress-track mt-5 h-1.5 w-full">
          <motion.div
            className="progress-fill"
            initial={{ width: "0%" }}
            animate={{ width: "100%" }}
            transition={{ duration: LINES.length * 0.3 + 0.6, ease: "easeInOut" }}
          />
        </div>
      </motion.div>
    </div>
  );
}
