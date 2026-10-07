import { moodColor, moodLabel } from "../lib/status";

/**
 * The one memorable, deliberate visual choice for this app: a status strip
 * styled like a terminal prompt / CI build status line, because the whole
 * product simulates being a software engineer — this is the artifact those
 * users see every day. Everything else in the UI stays quiet by comparison.
 */
export default function StatusStrip({ company, project, sprintNumber, managerScore, managerName }) {
  const path = company && project
    ? `${slugify(company.name)}/${slugify(project.title)}`
    : "no-active-simulation";

  return (
    <div className="relative flex items-center gap-3 overflow-x-auto whitespace-nowrap border-b border-white/[0.06] bg-ink/70 px-4 py-2 font-mono text-[13px] backdrop-blur-xl">
      <span className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-brand-500/60 to-transparent" />
      <span className="select-none rounded bg-gradient-to-r from-brand-500 to-accent-cyan bg-clip-text font-bold text-transparent">⬢ aiwse</span>
      <span className="select-none text-text-faint">@sim</span>
      <span className="select-none text-text-faint">:</span>
      <span className="text-brand-300 drop-shadow-[0_0_8px_rgba(139,124,246,0.6)]">~/{path}</span>
      {sprintNumber ? (
        <span className="text-text-muted">
          (sprint {sprintNumber}/4)
        </span>
      ) : null}
      <span className="cursor-blink text-text">▍</span>

      {managerScore != null && (
        <span className="ml-auto flex items-center gap-2 text-text-muted">
          <span
            className="relative inline-flex h-2 w-2 rounded-full pulse-dot"
            style={{ backgroundColor: moodColor(managerScore), color: moodColor(managerScore) }}
          />
          manager {managerName ? `${managerName} ` : ""}
          <span style={{ color: moodColor(managerScore) }}>{moodLabel(managerScore)}</span>
        </span>
      )}
    </div>
  );
}

function slugify(str) {
  return str
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}
