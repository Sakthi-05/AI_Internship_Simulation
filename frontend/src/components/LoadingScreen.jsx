import { motion } from "framer-motion";

export default function LoadingScreen({ label = "Generating your internship simulation" }) {
  return (
    <div className="flex h-full min-h-[50vh] items-center justify-center p-6" role="status" aria-live="polite">
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="loading-card card w-full max-w-md p-6"
      >
        <div className="flex items-center gap-4">
          <span className="loading-spinner" aria-hidden="true" />
          <div><p className="eyebrow">Please wait</p><p className="mt-1 text-sm font-medium text-text">{label}</p></div>
        </div>
      </motion.div>
    </div>
  );
}
