import { useState } from "react";
import { motion } from "framer-motion";

/**
 * SubmissionModal - File upload and response submission for tasks.
 *
 * Props:
 *   task          - current task object
 *   onClose       - () => void - close the modal
 *   onSubmit      - (files, metadata) => Promise - submit handler
 *   isSubmitting  - boolean - loading state
 */
export default function SubmissionModal({ task, onClose, onSubmit, isSubmitting, hasEditorWork = false }) {
  const [files, setFiles] = useState([]);
  const [response, setResponse] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const maxFiles = 10;

  const addFiles = (nextFiles) => {
    if (!nextFiles?.length) {
      return;
    }

    setFiles((currentFiles) => {
      const existingKeys = new Set(
        currentFiles.map((file) => `${file.name}-${file.size}-${file.lastModified}`)
      );

      const uniqueFiles = nextFiles.filter((file) => {
        const key = `${file.name}-${file.size}-${file.lastModified}`;
        return !existingKeys.has(key);
      });

      return [...currentFiles, ...uniqueFiles].slice(0, maxFiles);
    });
  };

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files) {
      addFiles(Array.from(e.dataTransfer.files));
    }
  };

  const handleFileSelect = (e) => {
    if (e.target.files) {
      addFiles(Array.from(e.target.files));
      e.target.value = "";
    }
  };

  const removeFile = (index) => {
    setFiles((currentFiles) => currentFiles.filter((_, i) => i !== index));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const result = await onSubmit(files, { response: response.trim() });
    if (result) {
      onClose();
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="submission-title">
      <motion.div
        className="absolute inset-0 z-0 bg-black/60 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        onClick={onClose}
      />
      <motion.div
        initial={{ opacity: 0, scale: 0.94, y: 24 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
        className="card relative z-10 flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden border-brand-400/25! shadow-[0_40px_120px_-20px_rgba(0,0,0,0.9),0_0_60px_-20px_rgba(124,108,246,0.6)]"
      >
        <div className="pointer-events-none absolute inset-x-0 top-0 h-20 bg-gradient-to-b from-brand-500/15 to-transparent" />
        <div className="relative flex items-center justify-between gap-3 border-b border-white/[0.06] p-4">
          <div>
            <p className="eyebrow">Ship it 🚀</p>
            <h2 id="submission-title" className="font-display text-lg font-bold">
              {task?.status === "changes_requested" ? "Resubmit Task" : "Submit Task"}: {task?.title || "Current Task"}
            </h2>
          </div>
          <button onClick={onClose} className="btn-ghost">
            ✕ Close
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-text-muted mb-2">
              Work summary / Notes (optional)
            </label>
            <textarea
              className="input w-full min-h-[180px]"
              placeholder="Describe your approach, any challenges faced, or notes for the reviewer..."
              value={response}
              onChange={(e) => setResponse(e.target.value)}
            />
            {hasEditorWork && <p className="mt-2 text-xs text-status-completed">Your edited workspace code will be included with this submission.</p>}
          </div>

          <div>
            <label className="block text-sm font-medium text-text-muted mb-2">
              Attach Files (optional: code, documents, screenshots, etc.)
            </label>
            <div
              className={`border-2 border-dashed rounded-lg p-6 text-center transition-colors ${
                dragActive ? "border-status-todo bg-status-todo/5" : "border-border"
              }`}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
            >
              <input
                type="file"
                multiple
                onChange={handleFileSelect}
                className="hidden"
                id="file-upload"
                accept=".py,.js,.ts,.jsx,.tsx,.java,.cpp,.cs,.go,.rs,.json,.yaml,.yml,.md,.txt,.pdf,.zip,.png,.jpg,.jpeg,.gif"
              />
              <label htmlFor="file-upload" className="cursor-pointer">
                <svg
                  className="w-12 h-12 mx-auto text-text-faint mb-3"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={1.5}
                    d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                  />
                </svg>
                <p className="text-text-muted">
                  Drag & drop files here, or{" "}
                  <span className="text-status-todo underline">click to browse</span>
                </p>
                <p className="text-xs text-text-faint mt-1">Optional · up to 10 files. Screenshots and other evidence are accepted when they help explain your work.</p>
              </label>
            </div>

            {files.length > 0 && (
              <div className="mt-3 space-y-2">
                {files.map((file, index) => (
                  <div
                    key={`${file.name}-${file.size}-${file.lastModified}-${index}`}
                    className="flex items-center justify-between p-2 bg-surface-2 rounded border border-border"
                  >
                    <div className="flex items-center gap-2">
                      <svg
                        className="w-5 h-5 text-text-muted"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M15.172 7l-6.586 6.586a.5.5 0 00.707.708L13 11.707l5.586 5.586a.5.5 0 00.707-.708L14.414 7H9.414a1 1 0 01-.707-1.707L13.293 3.293A1 1 0 0114 2h5a2 2 0 012 2v6h-2z"
                        />
                      </svg>
                      <span className="text-sm truncate max-w-[200px]">{file.name}</span>
                    </div>
                    <span className="text-xs text-text-faint">
                      {(file.size / 1024).toFixed(1)} KB
                    </span>
                    <button
                      type="button"
                      onClick={() => removeFile(index)}
                      className="text-text-faint hover:text-status-blocked p-1"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M6 18L18 6M6 6l12 12"
                        />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="pt-4 border-t border-border flex gap-3 justify-end">
            <button type="button" onClick={onClose} className="btn-ghost" disabled={isSubmitting}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={isSubmitting || (files.length === 0 && !response.trim() && !hasEditorWork)}
              title={files.length === 0 && !response.trim() && !hasEditorWork ? "Attach a file, edit the workspace, or add work evidence to submit." : undefined}
            >
              {isSubmitting
                ? "Submitting..."
                : task?.status === "changes_requested"
                  ? "Resubmit for AI Review"
                  : "Submit for Review"}
            </button>
          </div>
        </form>
      </motion.div>
    </div>
  );
}
