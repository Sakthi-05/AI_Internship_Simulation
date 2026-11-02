import assert from "node:assert/strict";
import test from "node:test";
import {
  invalidateWorkspaceQueries,
  shouldRefreshWorkspaceForPush,
  WORKSPACE_PUSH_TYPES,
  WORKSPACE_QUERY_KEYS,
} from "./workspaceSync.js";

test("workspace refresh covers task, sprint, workplace, chat, and evidence views once", async () => {
  const calls = [];
  const previousWindow = globalThis.window;
  const previousCustomEvent = globalThis.CustomEvent;
  const events = [];
  globalThis.window = { dispatchEvent: (event) => events.push(event.type) };
  globalThis.CustomEvent = class { constructor(type) { this.type = type; } };
  try {
    await invalidateWorkspaceQueries({ invalidateQueries: (query) => calls.push(query.queryKey) });
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
    if (previousCustomEvent === undefined) delete globalThis.CustomEvent;
    else globalThis.CustomEvent = previousCustomEvent;
  }
  const names = calls.map(([name]) => name);
  assert.equal(new Set(names).size, names.length);
  for (const name of [
    "simulation", "simulationProgress", "currentTask", "board", "projectState",
    "taskEvents", "events", "notifications", "timeline", "chat",
  ]) {
    assert.ok(names.includes(name), `missing ${name} invalidation`);
  }
  assert.equal(calls.length, WORKSPACE_QUERY_KEYS.length);
  assert.deepEqual(events, ["aiwse:workspace-updated"]);
});

test("persisted WebSocket event types trigger cross-view refresh", () => {
  for (const type of WORKSPACE_PUSH_TYPES) assert.equal(shouldRefreshWorkspaceForPush(type), true);
  assert.equal(shouldRefreshWorkspaceForPush("keepalive"), false);
});
