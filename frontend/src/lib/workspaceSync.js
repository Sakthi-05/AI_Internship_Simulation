/** Query families backed by persisted workplace state. Keep live views aligned
 * after local mutations and company WebSocket notifications. */
export const WORKSPACE_QUERY_KEYS = [
  ["simulation"],
  ["simulationProgress"],
  ["simulationList"],
  ["currentTask"],
  ["currentScenario"],
  ["simulationTasks"],
  ["taskEvents"],
  ["board"],
  ["projectState"],
  ["sprints"],
  ["events"],
  ["notifications"],
  ["timeline"],
  ["activity"],
  ["chat"],
];

export const WORKSPACE_PUSH_TYPES = [
  "task_updated", "bug_reported", "chat_message", "event", "notification",
];

export function shouldRefreshWorkspaceForPush(type) {
  return WORKSPACE_PUSH_TYPES.includes(type);
}

export function invalidateWorkspaceQueries(queryClient) {
  const updates = WORKSPACE_QUERY_KEYS.map((queryKey) =>
    queryClient.invalidateQueries({ queryKey })
  );
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("aiwse:workspace-updated"));
  }
  return Promise.all(updates);
}
