function currentVersion(task) {
  const versions = task?.criteria_version?.versions;
  if (!Array.isArray(versions) || versions.length === 0) return null;
  return versions.find((version) => version.status === "active") || versions[versions.length - 1];
}

export function criteriaLines(task) {
  const current = currentVersion(task);
  if (current) {
    return (current.items || [])
      .filter((item) => item && item.text && item.state !== "superseded")
      .map((item) => ({ text: String(item.text), state: item.state || "active" }));
  }
  const raw = task?.expected_output || task?.acceptance_criteria || [];
  return raw
    .filter((text) => typeof text === "string" && text)
    .map((text) => ({ text, state: "active" }));
}

export function earlierCriteria(task) {
  const versions = task?.criteria_version?.versions;
  if (!Array.isArray(versions)) return [];
  return versions
    .filter((version) => version.status === "superseded")
    .map((version) => ({
      versionNumber: version.version_number,
      items: (version.items || [])
        .filter((item) => item && item.text)
        .map((item) => String(item.text)),
    }));
}

export function criteriaStateLabel(state) {
  if (state === "modified") return "Modified";
  if (state === "superseded") return "Superseded";
  return "Active";
}
