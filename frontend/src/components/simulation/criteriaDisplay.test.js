import assert from "node:assert/strict";
import test from "node:test";
import { criteriaLines, earlierCriteria } from "./criteriaDisplay.js";

test("unversioned tasks show the flat criteria list as active", () => {
  const lines = criteriaLines({ expected_output: ["Returns the order list"] });
  assert.deepEqual(lines, [{ text: "Returns the order list", state: "active" }]);
  assert.deepEqual(earlierCriteria({ expected_output: ["Returns the order list"] }), []);
});

test("the active version hides superseded items and keeps modified ones", () => {
  const task = {
    acceptance_criteria: ["Returns the order list", "Validate invalid input"],
    criteria_version: {
      active_version_number: 3,
      versions: [
        {
          version_number: 1,
          status: "superseded",
          items: [{ text: "Returns the order list", state: "active" }],
        },
        {
          version_number: 2,
          status: "superseded",
          items: [
            { text: "Returns the order list", state: "active" },
            { text: "Validate invalid input", state: "modified" },
          ],
        },
        {
          version_number: 3,
          status: "active",
          items: [
            { text: "Returns the order list", state: "active" },
            { text: "Validate invalid input", state: "active" },
            { text: "Document the error cases", state: "modified" },
            { text: "Retired draft", state: "superseded" },
          ],
        },
      ],
    },
  };
  assert.deepEqual(criteriaLines(task).map((line) => line.text), [
    "Returns the order list",
    "Validate invalid input",
    "Document the error cases",
  ]);
  assert.equal(criteriaLines(task)[2].state, "modified");
  const earlier = earlierCriteria(task);
  assert.deepEqual(earlier.map((version) => version.versionNumber), [1, 2]);
  assert.equal(earlier[0].items[0], "Returns the order list");
});
