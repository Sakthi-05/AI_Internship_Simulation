import assert from "node:assert/strict";
import test from "node:test";
import {
  COMPANY_TYPES,
  DEFAULT_TEMPLATE_FORM,
  parseTechStack,
  validateCompanyTemplate,
  prepareTemplatePayload,
  buildCompanyTemplateParams,
  parseCompanyTemplatesResponse,
} from "./companyTemplates.js";

test("COMPANY_TYPES contains the required standard enterprise domains", () => {
  const values = COMPANY_TYPES.map((t) => t.value);
  assert.ok(values.includes("startup"));
  assert.ok(values.includes("product_company"));
  assert.ok(values.includes("mnc"));
  assert.ok(values.includes("healthcare"));
  assert.ok(values.includes("banking"));
  assert.ok(values.includes("ecommerce"));
  assert.ok(values.includes("education"));
});

test("parseTechStack handles arrays, strings, and whitespace trimming", () => {
  assert.deepEqual(parseTechStack("Python, React,   FastAPI , "), ["Python", "React", "FastAPI"]);
  assert.deepEqual(parseTechStack(["Docker", " Kubernetes ", ""]), ["Docker", "Kubernetes"]);
  assert.deepEqual(parseTechStack(null), []);
  assert.deepEqual(parseTechStack(""), []);
});

test("validateCompanyTemplate catches missing required fields", () => {
  const emptyForm = { ...DEFAULT_TEMPLATE_FORM };
  const errors = validateCompanyTemplate(emptyForm);
  assert.ok(errors);
  assert.ok(errors.name, "Name should be flagged as required");
  assert.ok(errors.industry, "Industry should be flagged as required");
  assert.ok(errors.department, "Department should be flagged as required");
  assert.ok(errors.mission, "Mission should be flagged as required");
  assert.ok(errors.description, "Description should be flagged as required");
});

test("validateCompanyTemplate validates company_type against allowed enum", () => {
  const invalidTypeForm = {
    name: "Valid Corp",
    company_type: "unknown_future_corp",
    industry: "Tech",
    department: "R&D",
    mission: "Innovate",
    description: "Doing things",
  };
  const errors = validateCompanyTemplate(invalidTypeForm);
  assert.ok(errors);
  assert.ok(errors.company_type);
});

test("validateCompanyTemplate passes with clean input", () => {
  const validForm = {
    name: "Nexus Labs",
    company_type: "startup",
    industry: "Artificial Intelligence",
    department: "Engineering",
    mission: "Accelerate software development with autonomous agents.",
    description: "A seed-stage engineering team developing developer tooling.",
    size: "small",
    technology_stack: "Python, FastAPI, Postgres",
  };
  const errors = validateCompanyTemplate(validForm);
  assert.equal(errors, null);
});

test("prepareTemplatePayload trims fields and structures technology_stack as array", () => {
  const form = {
    name: "  Acme Corp  ",
    company_type: "mnc",
    industry: "  FinTech  ",
    department: "  Payments  ",
    mission: "  Safe transactions everywhere.  ",
    description: "  Global banking services.  ",
    size: "large",
    technology_stack: "Java, Spring Boot, Kafka",
  };
  const payload = prepareTemplatePayload(form);
  assert.deepEqual(payload, {
    name: "Acme Corp",
    company_type: "mnc",
    industry: "FinTech",
    department: "Payments",
    mission: "Safe transactions everywhere.",
    description: "Global banking services.",
    size: "large",
    technology_stack: ["Java", "Spring Boot", "Kafka"],
  });
});

test("buildCompanyTemplateParams strips empty filters and formats pagination", () => {
  const params = buildCompanyTemplateParams({
    q: "Acme",
    company_type: "",
    include_inactive: false,
    page: 2,
    page_size: 10,
  });
  assert.deepEqual(params, {
    q: "Acme",
    page: 2,
    page_size: 10,
  });

  const inactiveParams = buildCompanyTemplateParams({
    include_inactive: true,
    company_type: "startup",
  });
  assert.deepEqual(inactiveParams, {
    include_inactive: true,
    company_type: "startup",
  });
});

test("parseCompanyTemplatesResponse normalizes malformed or null responses safely", () => {
  const parsed = parseCompanyTemplatesResponse(null);
  assert.deepEqual(parsed, {
    templates: [],
    pagination: { page: 1, page_size: 25, total: 0, pages: 1 },
  });

  const validResponse = {
    templates: [
      { id: "1", name: "Company A", is_active: true },
      { id: "2", name: "Company B", is_active: false },
    ],
    pagination: { page: 1, page_size: 25, total: 2, pages: 1 },
  };
  const normalized = parseCompanyTemplatesResponse(validResponse);
  assert.equal(normalized.templates.length, 2);
  assert.equal(normalized.pagination.total, 2);
});
