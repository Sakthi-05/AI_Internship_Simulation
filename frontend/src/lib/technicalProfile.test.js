import assert from "node:assert/strict";
import test from "node:test";
import {
  buildTechnicalProfilePayload,
  getDomainTechnologies,
  loadTechnicalProfile,
  ratingsFromTechnicalProfile,
  saveTechnicalProfile,
  technicalProfileState,
} from "./technicalProfile.js";

const catalog = {
  domains: [
    { id: "alpha", name: "Alpha", technologies: [{ id: "a", name: "A" }, { id: "b", name: "B" }] },
    { id: "beta", name: "Beta", technologies: [{ id: "c", name: "C" }] },
  ],
};

test("loads the domain catalog and existing saved profile through the API client", async () => {
  const calls = [];
  const existing = {
    selected: true,
    status: "active",
    technical_domain_id: "alpha",
    technology_ratings: [{ technology_id: "a", self_rating: 4 }],
  };
  const result = await loadTechnicalProfile({
    get: async (path) => {
      calls.push(path);
      return { data: path === "/api/technical-domains" ? catalog : existing };
    },
  });
  assert.deepEqual(calls, ["/api/technical-domains", "/api/students/me/technical-profile"]);
  assert.deepEqual(result.domains, catalog.domains);
  assert.equal(result.profile, existing);
  assert.deepEqual(ratingsFromTechnicalProfile(result.profile), { a: 4 });
  assert.equal(technicalProfileState(result.profile), "active");
});

test("changing the selected domain returns only that domain's technologies", () => {
  assert.deepEqual(getDomainTechnologies(catalog.domains, "alpha"), catalog.domains[0].technologies);
  assert.deepEqual(getDomainTechnologies(catalog.domains, "beta"), catalog.domains[1].technologies);
  assert.deepEqual(getDomainTechnologies(catalog.domains, "unknown"), []);
});

test("builds a complete 1-to-5 submission payload and rejects incomplete ratings", () => {
  const payload = buildTechnicalProfilePayload("alpha", catalog.domains[0].technologies, { a: 1, b: 5 });
  assert.deepEqual(payload, {
    technical_domain_id: "alpha",
    technology_ratings: [
      { technology_id: "a", self_rating: 1 },
      { technology_id: "b", self_rating: 5 },
    ],
  });
  assert.throws(() => buildTechnicalProfilePayload("alpha", catalog.domains[0].technologies, { a: 4 }), /Rate every technology/);
  assert.throws(() => buildTechnicalProfilePayload("alpha", catalog.domains[0].technologies, { a: 4, b: 6 }), /Rate every technology/);
});

test("unavailable saved domains are surfaced without choosing a replacement", () => {
  const saved = { selected: true, status: "unavailable", technical_domain_id: "retired" };
  assert.equal(technicalProfileState(saved), "unavailable");
  assert.equal(saved.technical_domain_id, "retired");
  assert.equal(technicalProfileState({ selected: false, technical_domain_id: null }), "unselected");
});

test("catalog load errors and failed saves are propagated to the UI layer", async () => {
  const apiError = new Error("catalog unavailable");
  await assert.rejects(loadTechnicalProfile({ get: async () => { throw apiError; } }), /catalog unavailable/);
  const saveError = new Error("save failed");
  await assert.rejects(saveTechnicalProfile({ put: async () => { throw saveError; } }, {}), /save failed/);
});

test("saves the expected endpoint and returns the persisted response", async () => {
  const payload = { technical_domain_id: "beta", technology_ratings: [{ technology_id: "c", self_rating: 3 }] };
  const saved = { selected: true, status: "active", ...payload };
  const calls = [];
  const response = await saveTechnicalProfile({
    put: async (...args) => {
      calls.push(args);
      return { data: saved };
    },
  }, payload);
  assert.deepEqual(calls, [["/api/students/me/technical-profile", payload]]);
  assert.equal(response, saved);
});
