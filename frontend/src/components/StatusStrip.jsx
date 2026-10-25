import { moodColor, moodLabel } from "../lib/status";

export default function StatusStrip({ company, project, sprintNumber, managerScore, managerName }) {
  const path = company && project
    ? `${slugify(company.name)}/${slugify(project.title)}`
    : "no-active-simulation";

  return (
    <div className="flex min-w-0 items-center gap-2 whitespace-nowrap text-xs">
      <span className="font-semibold text-text">{company?.name || "Internship"}</span>
      <span className="text-text-faint">/</span>
      <span className="max-w-[300px] truncate text-text-muted">{project?.title || path}</span>
      {sprintNumber ? <span className="rounded-md bg-surface-3 px-2 py-1 text-[10px] font-medium text-text-muted">Sprint {sprintNumber} / 4</span> : null}

      {managerScore != null && (
        <span className="ml-auto flex items-center gap-2 text-text-muted">
          <span
            className="relative inline-flex h-2 w-2 rounded-full pulse-dot"
            style={{ backgroundColor: moodColor(managerScore), color: moodColor(managerScore) }}
          />
          {managerName ? `${managerName} · ` : "Manager · "}
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
