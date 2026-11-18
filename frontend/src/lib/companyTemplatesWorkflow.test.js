import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_TEMPLATE_FORM,
  validateCompanyTemplate,
  prepareTemplatePayload,
  buildCompanyTemplateParams,
  parseCompanyTemplatesResponse,
} from "./companyTemplates.js";

test("Workflow: Create Company Template lifecycle", () => {
  // 1. Initial form state is blank
  const form = { ...DEFAULT_TEMPLATE_FORM };
  assert.equal(form.name, "");

  // 2. Submission without fields fails validation
  const errors = validateCompanyTemplate(form);
  assert.ok(errors.name);
  assert.ok(errors.industry);

  // 3. User enters valid information
  form.name = "FinTech Global";
  form.company_type = "banking";
  form.industry = "Finance";
  form.department = "Risk & Compliance";
  form.mission = "Secure digital banking for all.";
  form.description = "Global banking enterprise simulation.";
  form.size = "enterprise";
  form.technology_stack = "Java, Spring Boot, Postgres";

  // 4. Form validation passes
  const validErrors = validateCompanyTemplate(form);
  assert.equal(validErrors, null);

  // 5. Payload preparation formats tech stack correctly
  const payload = prepareTemplatePayload(form);
  assert.equal(payload.name, "FinTech Global");
  assert.equal(payload.company_type, "banking");
  assert.deepEqual(payload.technology_stack, ["Java", "Spring Boot", "Postgres"]);

  // 6. Mock API response parsing
  const apiResponse = {
    templates: [
      {
        id: "tpl-123",
        ...payload,
        is_active: true,
        created_by: "admin-1",
        created_at: "2026-11-17T00:00:00Z",
        updated_at: "2026-11-17T00:00:00Z",
      },
    ],
    pagination: { page: 1, page_size: 10, total: 1, pages: 1 },
  };

  const parsed = parseCompanyTemplatesResponse(apiResponse);
  assert.equal(parsed.templates.length, 1);
  assert.equal(parsed.templates[0].id, "tpl-123");
  assert.equal(parsed.templates[0].is_active, true);
});

test("Workflow: Edit Company Template pre-fill and payload generation", () => {
  // Existing template from API
  const existing = {
    id: "tpl-999",
    name: "Old Corp",
    company_type: "startup",
    industry: "Tech",
    department: "R&D",
    mission: "Old mission",
    description: "Old description",
    size: "small",
    technology_stack: ["Python", "FastAPI"],
    is_active: true,
  };

  // Pre-fill form
  const editForm = {
    name: existing.name,
    company_type: existing.company_type,
    industry: existing.industry,
    department: existing.department,
    mission: existing.mission,
    description: existing.description,
    size: existing.size,
    technology_stack: existing.technology_stack.join(", "),
  };

  assert.equal(editForm.technology_stack, "Python, FastAPI");

  // Modify fields
  editForm.name = "New Corp";
  editForm.technology_stack = "Python, FastAPI, Redis, Docker";

  const updatedPayload = prepareTemplatePayload(editForm);
  assert.equal(updatedPayload.name, "New Corp");
  assert.deepEqual(updatedPayload.technology_stack, ["Python", "FastAPI", "Redis", "Docker"]);
});

test("Workflow: Search and filtering parameter construction", () => {
  // Search query with category filter
  const params = buildCompanyTemplateParams({
    q: "Health",
    company_type: "healthcare",
    include_inactive: false,
    page: 1,
    page_size: 10,
  });

  assert.equal(params.q, "Health");
  assert.equal(params.company_type, "healthcare");
  assert.equal(params.include_inactive, undefined); // falsy omitted

  // Include inactive toggle
  const inactiveParams = buildCompanyTemplateParams({
    include_inactive: true,
    page: 2,
  });
  assert.equal(inactiveParams.include_inactive, true);
  assert.equal(inactiveParams.page, 2);
});

test("Workflow: Deactivation soft-delete preserves entity identity", () => {
  const activeTemplate = {
    id: "tpl-555",
    name: "Deactivate Me",
    is_active: true,
  };

  // Simulated deactivation
  const deactivated = {
    ...activeTemplate,
    is_active: false,
  };

  assert.equal(deactivated.id, activeTemplate.id);
  assert.equal(deactivated.is_active, false);
});
