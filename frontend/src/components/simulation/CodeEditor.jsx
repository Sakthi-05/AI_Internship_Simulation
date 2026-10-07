import { useState, useRef, useEffect } from "react";

const DEFAULT_CODE = `// Write your solution here
function solution() {
  // TODO: Implement
}
`;

const LANG_MAP = {
  coding: "javascript",
  debugging: "javascript",
  database: "sql",
  api_development: "javascript",
  testing: "javascript",
  documentation: "markdown",
  requirement_analysis: "markdown",
  design: "markdown",
  decision_making: "markdown",
};

function inferLanguage(task, project) {
  const stack = (project?.technology_stack || []).map((s) => String(s).toLowerCase());
  const haystack = stack.join(" ");
  if (/\bpython\b/.test(haystack)) return "python";
  if (/\bsql\b|postgres|mysql/.test(haystack) || task?.task_type === "database") return "sql";
  if (/\btypescript\b|\bts\b/.test(haystack)) return "typescript";
  if (/\bjava\b/.test(haystack)) return "java";
  if (/\bgo\b|golang/.test(haystack)) return "go";
  if (/\brust\b/.test(haystack)) return "rust";
  return LANG_MAP[task?.task_type] || "javascript";
}

function templateFor(language, task) {
  if (language === "python") {
    return `# ${task?.title || "Task"} — write your solution here\ndef solution():\n    # TODO: Implement\n    pass\n`;
  }
  if (language === "sql") {
    return `-- ${task?.title || "Task"} — write your query here\nSELECT 1;\n`;
  }
  if (language === "markdown") {
    return `# ${task?.title || "Task"}\n\n## Approach\n\n## Notes\n`;
  }
  return DEFAULT_CODE;
}

export default function CodeEditor({ task, project, onCodeChange, onWorkChange, initialCode, readOnly = false }) {
  const taskId = task?.task_id || task?.id;
  const projectId = project?.id;
  const storageKey = `aiwse:code:${projectId || "noproject"}:${taskId || "notask"}`;
  const language = inferLanguage(task, project);
  const fallbackCode = initialCode || templateFor(language, task);
  const [code, setCode] = useState(() => {
    try {
      const draft = localStorage.getItem(storageKey);
      if (draft) return draft;
    } catch {
      // ignore storage errors and fall through to defaults
    }
    return fallbackCode;
  });
  const [hasWork, setHasWork] = useState(() => {
    try {
      const draft = localStorage.getItem(storageKey);
      return Boolean(draft && draft.trim() && draft !== fallbackCode);
    } catch {
      return false;
    }
  });
  const [fontSize, setFontSize] = useState(14);
  const textareaRef = useRef(null);

  // Reload the draft when the current task/project changes so the workspace
  // stays specific to the active task.
  useEffect(() => {
    try {
      const draft = localStorage.getItem(storageKey);
      const nextCode = draft || fallbackCode;
      setCode(nextCode);
      setHasWork(Boolean(draft && draft.trim() && draft !== fallbackCode));
    } catch {
      setCode(fallbackCode);
      setHasWork(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, code);
    } catch {
      // ignore quota errors
    }
    if (onCodeChange) onCodeChange(code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  useEffect(() => {
    onWorkChange?.(hasWork);
  }, [hasWork, onWorkChange]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = textareaRef.current.scrollHeight + "px";
    }
  }, [code]);

  const handleKeyDown = (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const start = e.target.selectionStart;
      const end = e.target.selectionEnd;
      const newCode = code.substring(0, start) + "  " + code.substring(end);
      setCode(newCode);
      setTimeout(() => {
        e.target.selectionStart = e.target.selectionEnd = start + 2;
      }, 0);
    }
  };

  const lines = code.split("\n");

  const stack = project?.technology_stack || [];
  const fileExt = language === "python" ? "py" : language === "sql" ? "sql" : language === "markdown" ? "md" : "js";

  return (
    <div className="card flex flex-col overflow-hidden" style={{ height: "100%" }}>
      {(project || task) && (
        <div className="px-3 py-2 border-b border-border bg-surface-2/70">
          <p className="text-xs font-medium truncate" title={task?.title}>
            {project?.title ? `${project.title} — ` : ""}{task?.title || "Current task"}
            <span className="text-text-faint font-mono"> · solution.{fileExt}</span>
          </p>
          {stack.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {stack.slice(0, 5).map((tech) => (
                <span key={tech} className="px-1.5 py-0.5 rounded bg-surface-3 text-[10px] text-text-muted">
                  {tech}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-surface-2/50">
        <div className="flex items-center gap-2">
          <span className="px-2 py-0.5 rounded text-[10px] font-mono uppercase tracking-wide bg-status-todo/10 text-status-todo">
            {language}
          </span>
          <span className="text-xs text-text-faint">{lines.length} lines</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setFontSize((s) => Math.max(10, s - 2))}
            className="w-6 h-6 flex items-center justify-center rounded text-text-faint hover:bg-surface-2"
          >
            A-
          </button>
          <span className="text-[10px] text-text-faint font-mono">{fontSize}px</span>
          <button
            onClick={() => setFontSize((s) => Math.min(24, s + 2))}
            className="w-6 h-6 flex items-center justify-center rounded text-text-faint hover:bg-surface-2"
          >
            A+
          </button>
        </div>
      </div>

      <div className="flex-1 flex overflow-auto bg-[#1e1e1e]">
        <div className="py-3 px-2 text-right select-none bg-[#1a1a2e] border-r border-border/20">
          {lines.map((_, i) => (
            <div key={i} className="text-[11px] font-mono leading-[1.6] text-text-faint/50">
              {i + 1}
            </div>
          ))}
        </div>
        <textarea
          ref={textareaRef}
          value={code}
          onChange={(e) => {
            if (readOnly) return;
            const nextCode = e.target.value;
            setCode(nextCode);
            setHasWork(Boolean(nextCode.trim() && nextCode !== fallbackCode));
          }}
          onKeyDown={handleKeyDown}
          readOnly={readOnly}
          aria-readonly={readOnly}
          title={readOnly ? "Start the task to edit your work." : undefined}
          spellCheck={false}
          className="flex-1 bg-transparent text-[#d4d4d4] p-3 font-mono resize-none outline-none leading-[1.6]"
          style={{ fontSize: `${fontSize}px`, tabSize: 2, minHeight: "100%" }}
        />
      </div>
    </div>
  );
}
