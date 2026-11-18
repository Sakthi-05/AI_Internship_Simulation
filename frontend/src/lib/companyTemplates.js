/**
 * Utilities, validation, and data formatting for Admin Company Templates.
 */

export const COMPANY_TYPES = [
  { value: "startup", label: "Startup" },
  { value: "product_company", label: "Product Company" },
  { value: "mnc", label: "MNC" },
  { value: "healthcare", label: "Healthcare" },
  { value: "banking", label: "Banking" },
  { value: "ecommerce", label: "E-Commerce" },
  { value: "education", label: "Education" },
];

export const COMPANY_SIZES = [
  { value: "small", label: "Small (< 50)" },
  { value: "medium", label: "Medium (50–250)" },
  { value: "large", label: "Large (250–1000)" },
  { value: "enterprise", label: "Enterprise (1000+)" },
];

export const DEFAULT_TEMPLATE_FORM = {
  name: "",
  company_type: "startup",
  industry: "",
  department: "",
  mission: "",
  description: "",
  size: "small",
  technology_stack: "",
};

/**
 * Parses a comma-separated string or array into a clean list of tech strings.
 */
export function parseTechStack(input) {
  if (Array.isArray(input)) {
    return input.map((s) => String(s).trim()).filter(Boolean);
  }
  if (typeof input === "string") {
    return input
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return [];
}

/**
 * Validates company template form inputs. Returns an object mapping field names
 * to error strings, or null if valid.
 */
export function validateCompanyTemplate(form) {
  const errors = {};

  if (!form || typeof form !== "object") {
    return { form: "Invalid form data" };
  }

  const name = (form.name || "").trim();
  if (!name) {
    errors.name = "Company name is required";
  } else if (name.length > 200) {
    errors.name = "Company name cannot exceed 200 characters";
  }

  const validTypes = COMPANY_TYPES.map((t) => t.value);
  if (!form.company_type || !validTypes.includes(form.company_type)) {
    errors.company_type = `Company type must be one of: ${validTypes.join(", ")}`;
  }

  const industry = (form.industry || "").trim();
  if (!industry) {
    errors.industry = "Industry is required";
  } else if (industry.length > 150) {
    errors.industry = "Industry cannot exceed 150 characters";
  }

  const department = (form.department || "").trim();
  if (!department) {
    errors.department = "Department is required";
  } else if (department.length > 150) {
    errors.department = "Department cannot exceed 150 characters";
  }

  const mission = (form.mission || "").trim();
  if (!mission) {
    errors.mission = "Mission statement is required";
  }

  const description = (form.description || "").trim();
  if (!description) {
    errors.description = "Description is required";
  }

  return Object.keys(errors).length > 0 ? errors : null;
}

/**
 * Prepares payload for POST/PUT requests to /api/admin/companies.
 */
export function prepareTemplatePayload(form) {
  return {
    name: (form.name || "").trim(),
    company_type: form.company_type || "startup",
    industry: (form.industry || "").trim(),
    department: (form.department || "").trim(),
    mission: (form.mission || "").trim(),
    description: (form.description || "").trim(),
    size: (form.size || "small").trim(),
    technology_stack: parseTechStack(form.technology_stack),
  };
}

/**
 * Builds clean params for GET /api/admin/companies.
 */
export function buildCompanyTemplateParams(filters = {}) {
  const params = {};
  if (filters.q && filters.q.trim()) {
    params.q = filters.q.trim();
  }
  if (filters.company_type && filters.company_type.trim()) {
    params.company_type = filters.company_type.trim();
  }
  if (filters.include_inactive) {
    params.include_inactive = true;
  }
  if (filters.page && Number(filters.page) > 0) {
    params.page = Number(filters.page);
  }
  if (filters.page_size && Number(filters.page_size) > 0) {
    params.page_size = Number(filters.page_size);
  }
  return params;
}

/**
 * Normalizes backend response from GET /api/admin/companies.
 */
export function parseCompanyTemplatesResponse(data) {
  if (!data || typeof data !== "object") {
    return { templates: [], pagination: { page: 1, page_size: 25, total: 0, pages: 1 } };
  }
  return {
    templates: Array.isArray(data.templates) ? data.templates : [],
    pagination: {
      page: Number(data.pagination?.page) || 1,
      page_size: Number(data.pagination?.page_size) || 25,
      total: Number(data.pagination?.total) || 0,
      pages: Number(data.pagination?.pages) || 1,
    },
  };
}
