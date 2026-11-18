import { useCallback, useEffect, useState } from "react";
import { apiClient } from "../api/client";
import {
  COMPANY_TYPES,
  COMPANY_SIZES,
  DEFAULT_TEMPLATE_FORM,
  validateCompanyTemplate,
  prepareTemplatePayload,
  buildCompanyTemplateParams,
  parseCompanyTemplatesResponse,
} from "../lib/companyTemplates";

export default function CompanyTemplates({ defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  const [templates, setTemplates] = useState([]);
  const [pagination, setPagination] = useState({ page: 1, page_size: 10, total: 0, pages: 1 });
  const [filters, setFilters] = useState({ q: "", company_type: "", include_inactive: false });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Modal state
  const [modalMode, setModalMode] = useState(null); // 'create' | 'edit' | null
  const [activeTemplate, setActiveTemplate] = useState(null);
  const [form, setForm] = useState(DEFAULT_TEMPLATE_FORM);
  const [formErrors, setFormErrors] = useState({});
  const [saving, setSaving] = useState(false);

  // Deactivation confirmation state
  const [deactivatingId, setDeactivatingId] = useState(null);

  const loadTemplates = useCallback(async (page = 1) => {
    setLoading(true);
    setError("");
    try {
      const params = buildCompanyTemplateParams({
        ...filters,
        page,
        page_size: pagination.page_size,
      });
      const response = await apiClient.get("/api/admin/companies", { params });
      const parsed = parseCompanyTemplatesResponse(response.data);
      setTemplates(parsed.templates);
      setPagination(parsed.pagination);
    } catch (err) {
      setError(err.message || "Failed to load company templates");
    } finally {
      setLoading(false);
    }
  }, [filters, pagination.page_size]);

  useEffect(() => {
    if (open) {
      const timer = setTimeout(() => {
        loadTemplates(1);
      }, 250);
      return () => clearTimeout(timer);
    }
  }, [open, loadTemplates]);

  const openCreateModal = () => {
    setModalMode("create");
    setActiveTemplate(null);
    setForm(DEFAULT_TEMPLATE_FORM);
    setFormErrors({});
    setError("");
    setSuccess("");
  };

  const openEditModal = (template) => {
    setModalMode("edit");
    setActiveTemplate(template);
    setForm({
      name: template.name || "",
      company_type: template.company_type || "startup",
      industry: template.industry || "",
      department: template.department || "",
      mission: template.mission || "",
      description: template.description || "",
      size: template.size || "small",
      technology_stack: Array.isArray(template.technology_stack)
        ? template.technology_stack.join(", ")
        : "",
    });
    setFormErrors({});
    setError("");
    setSuccess("");
  };

  const closeModal = () => {
    setModalMode(null);
    setActiveTemplate(null);
    setFormErrors({});
  };

  const handleFormChange = (field, value) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    if (formErrors[field]) {
      setFormErrors((prev) => ({ ...prev, [field]: null }));
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const validationErrors = validateCompanyTemplate(form);
    if (validationErrors) {
      setFormErrors(validationErrors);
      return;
    }

    setSaving(true);
    setError("");
    setSuccess("");
    try {
      const payload = prepareTemplatePayload(form);
      if (modalMode === "create") {
        await apiClient.post("/api/admin/companies", payload);
        setSuccess(`Company template "${payload.name}" created successfully.`);
      } else if (modalMode === "edit" && activeTemplate) {
        await apiClient.put(`/api/admin/companies/${activeTemplate.id}`, payload);
        setSuccess(`Company template "${payload.name}" updated successfully.`);
      }
      closeModal();
      await loadTemplates(pagination.page);
    } catch (err) {
      setError(err.message || "Failed to save company template");
    } finally {
      setSaving(false);
    }
  };

  const handleDeactivate = async (templateId, templateName) => {
    setError("");
    setSuccess("");
    try {
      await apiClient.delete(`/api/admin/companies/${templateId}`);
      setSuccess(`Company template "${templateName}" deactivated successfully.`);
      setDeactivatingId(null);
      await loadTemplates(pagination.page);
    } catch (err) {
      setError(err.message || "Failed to deactivate template");
    }
  };

  return (
    <section className="card overflow-hidden">
      <button
        type="button"
        className="flex w-full items-center justify-between p-5 text-left transition hover:bg-white/[0.02]"
        onClick={() => setOpen((prev) => !prev)}
      >
        <span>
          <span className="eyebrow block">Enterprise Simulation Architecture</span>
          <span className="mt-1 block font-display text-lg font-semibold">
            Company Templates Bank
          </span>
          <span className="mt-0.5 text-xs text-text-muted">
            Manage reusable corporate templates and industry presets for student simulations.
          </span>
        </span>
        <span className="chip">{open ? "Hide Bank" : "Manage Templates"}</span>
      </button>

      {open && (
        <div className="space-y-4 border-t border-white/[0.06] p-5">
          {/* Header Action Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="input min-w-48 text-sm"
                placeholder="Search templates..."
                value={filters.q}
                onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
              />
              <select
                className="input text-sm"
                value={filters.company_type}
                onChange={(e) => setFilters((f) => ({ ...f, company_type: e.target.value }))}
              >
                <option value="">All Company Types</option>
                {COMPANY_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer select-none">
                <input
                  type="checkbox"
                  className="rounded border-white/20 bg-ink/50 text-brand-500"
                  checked={filters.include_inactive}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, include_inactive: e.target.checked }))
                  }
                />
                Show Inactive
              </label>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                className="btn-ghost text-xs"
                onClick={() => loadTemplates(pagination.page)}
                disabled={loading}
              >
                {loading ? "Refreshing..." : "↻ Refresh"}
              </button>
              <button
                type="button"
                className="btn-primary text-xs"
                onClick={openCreateModal}
              >
                + New Template
              </button>
            </div>
          </div>

          {/* Feedback banners */}
          {error && (
            <div className="rounded-xl border border-status-blocked/30 bg-status-blocked/10 p-3 text-sm text-status-blocked">
              {error}
            </div>
          )}
          {success && (
            <div className="rounded-xl border border-status-completed/30 bg-status-completed/10 p-3 text-sm text-status-completed">
              {success}
            </div>
          )}

          {/* Templates Table */}
          <div className="overflow-x-auto">
            <table className="table-premium w-full min-w-[800px] text-left text-sm">
              <thead>
                <tr className="border-b border-white/[0.06] bg-ink/40 text-[10px] uppercase tracking-wider text-text-faint">
                  <th className="p-3">Company Name</th>
                  <th className="p-3">Type & Scale</th>
                  <th className="p-3">Industry / Dept</th>
                  <th className="p-3">Technology Stack</th>
                  <th className="p-3">Status</th>
                  <th className="p-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {templates.map((tpl) => (
                  <tr
                    key={tpl.id}
                    className="border-b border-white/[0.04] last:border-0 hover:bg-white/[0.01]"
                  >
                    <td className="p-3">
                      <p className="font-medium text-text">{tpl.name}</p>
                      <p className="text-xs text-text-faint truncate max-w-xs">{tpl.mission}</p>
                    </td>
                    <td className="p-3 text-xs">
                      <span className="chip capitalize mr-1">{tpl.company_type?.replace("_", " ")}</span>
                      <span className="text-text-muted capitalize">({tpl.size})</span>
                    </td>
                    <td className="p-3 text-xs text-text-muted">
                      {tpl.industry} · {tpl.department}
                    </td>
                    <td className="p-3">
                      <div className="flex flex-wrap gap-1 max-w-xs">
                        {tpl.technology_stack?.slice(0, 4).map((tech) => (
                          <span
                            key={tech}
                            className="rounded-md bg-white/[0.04] px-1.5 py-0.5 text-[10px] text-text-muted border border-white/[0.05]"
                          >
                            {tech}
                          </span>
                        ))}
                        {(tpl.technology_stack?.length || 0) > 4 && (
                          <span className="text-[10px] text-text-faint">
                            +{tpl.technology_stack.length - 4}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="p-3 text-xs">
                      {tpl.is_active ? (
                        <span className="text-status-completed font-medium">Active</span>
                      ) : (
                        <span className="text-status-blocked font-medium">Inactive</span>
                      )}
                    </td>
                    <td className="p-3 text-right">
                      <div className="flex justify-end items-center gap-2">
                        <button
                          type="button"
                          className="btn-ghost text-xs py-1 px-2.5"
                          onClick={() => openEditModal(tpl)}
                        >
                          Edit
                        </button>
                        {tpl.is_active && (
                          <button
                            type="button"
                            className="btn-ghost text-xs text-status-blocked hover:bg-status-blocked/10 py-1 px-2.5"
                            onClick={() => setDeactivatingId(tpl.id)}
                          >
                            Deactivate
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!loading && templates.length === 0 && (
            <p className="p-8 text-center text-sm text-text-faint">
              No company templates found matching your criteria.
            </p>
          )}

          {/* Pagination */}
          {pagination.pages > 1 && (
            <div className="flex items-center justify-between border-t border-white/[0.06] pt-3 text-xs text-text-muted">
              <span>
                Page {pagination.page} of {pagination.pages} ({pagination.total} total)
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={pagination.page <= 1 || loading}
                  onClick={() => loadTemplates(pagination.page - 1)}
                >
                  Previous
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={pagination.page >= pagination.pages || loading}
                  onClick={() => loadTemplates(pagination.page + 1)}
                >
                  Next
                </button>
              </div>
            </div>
          )}

          {/* Create / Edit Modal */}
          {modalMode && (
            <div className="fixed inset-0 z-50 grid place-items-center bg-ink/80 p-4 backdrop-blur-sm">
              <div className="card w-full max-w-xl max-h-[90vh] overflow-y-auto p-6 space-y-4 shadow-2xl">
                <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
                  <h3 className="font-display text-lg font-semibold">
                    {modalMode === "create" ? "Create Reusable Company Template" : "Edit Company Template"}
                  </h3>
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    onClick={closeModal}
                  >
                    ✕
                  </button>
                </div>

                <form onSubmit={handleSubmit} className="space-y-4">
                  <div>
                    <label className="block text-xs text-text-muted mb-1">Company Name *</label>
                    <input
                      className="input w-full"
                      value={form.name}
                      onChange={(e) => handleFormChange("name", e.target.value)}
                      placeholder="e.g. Acme Cloud Systems"
                      required
                    />
                    {formErrors.name && (
                      <p className="mt-1 text-xs text-status-blocked">{formErrors.name}</p>
                    )}
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs text-text-muted mb-1">Company Type *</label>
                      <select
                        className="input w-full"
                        value={form.company_type}
                        onChange={(e) => handleFormChange("company_type", e.target.value)}
                      >
                        {COMPANY_TYPES.map((t) => (
                          <option key={t.value} value={t.value}>
                            {t.label}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div>
                      <label className="block text-xs text-text-muted mb-1">Scale / Size</label>
                      <select
                        className="input w-full"
                        value={form.size}
                        onChange={(e) => handleFormChange("size", e.target.value)}
                      >
                        {COMPANY_SIZES.map((s) => (
                          <option key={s.value} value={s.value}>
                            {s.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs text-text-muted mb-1">Industry *</label>
                      <input
                        className="input w-full"
                        value={form.industry}
                        onChange={(e) => handleFormChange("industry", e.target.value)}
                        placeholder="e.g. Enterprise SaaS"
                        required
                      />
                      {formErrors.industry && (
                        <p className="mt-1 text-xs text-status-blocked">{formErrors.industry}</p>
                      )}
                    </div>

                    <div>
                      <label className="block text-xs text-text-muted mb-1">Department *</label>
                      <input
                        className="input w-full"
                        value={form.department}
                        onChange={(e) => handleFormChange("department", e.target.value)}
                        placeholder="e.g. Core Infrastructure"
                        required
                      />
                      {formErrors.department && (
                        <p className="mt-1 text-xs text-status-blocked">{formErrors.department}</p>
                      )}
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs text-text-muted mb-1">Technology Stack</label>
                    <input
                      className="input w-full"
                      value={form.technology_stack}
                      onChange={(e) => handleFormChange("technology_stack", e.target.value)}
                      placeholder="Comma-separated: Python, FastAPI, React, PostgreSQL"
                    />
                    <p className="mt-0.5 text-[10px] text-text-faint">
                      Separate technologies with commas
                    </p>
                  </div>

                  <div>
                    <label className="block text-xs text-text-muted mb-1">Mission Statement *</label>
                    <textarea
                      className="input w-full min-h-16 text-sm"
                      value={form.mission}
                      onChange={(e) => handleFormChange("mission", e.target.value)}
                      placeholder="Core organizational mission and objectives..."
                      required
                    />
                    {formErrors.mission && (
                      <p className="mt-1 text-xs text-status-blocked">{formErrors.mission}</p>
                    )}
                  </div>

                  <div>
                    <label className="block text-xs text-text-muted mb-1">Description *</label>
                    <textarea
                      className="input w-full min-h-20 text-sm"
                      value={form.description}
                      onChange={(e) => handleFormChange("description", e.target.value)}
                      placeholder="Detailed context about the simulation work environment..."
                      required
                    />
                    {formErrors.description && (
                      <p className="mt-1 text-xs text-status-blocked">{formErrors.description}</p>
                    )}
                  </div>

                  <div className="flex justify-end gap-2 pt-3 border-t border-white/[0.06]">
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={closeModal}
                      disabled={saving}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      className="btn-primary"
                      disabled={saving}
                    >
                      {saving ? "Saving..." : modalMode === "create" ? "Create Template" : "Save Changes"}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}

          {/* Deactivation Confirmation Modal */}
          {deactivatingId && (
            <div className="fixed inset-0 z-50 grid place-items-center bg-ink/80 p-4 backdrop-blur-sm">
              <div className="card w-full max-w-md p-6 space-y-4 shadow-2xl">
                <h3 className="font-display text-lg font-semibold text-status-blocked">
                  Deactivate Company Template?
                </h3>
                <p className="text-sm text-text-muted leading-relaxed">
                  Deactivating will hide this template from new simulations. Existing student simulations
                  and generated historical records will not be affected.
                </p>
                <div className="flex justify-end gap-2 pt-3 border-t border-white/[0.06]">
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => setDeactivatingId(null)}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn-primary bg-status-blocked hover:bg-status-blocked/80"
                    onClick={() => {
                      const t = templates.find((x) => x.id === deactivatingId);
                      handleDeactivate(deactivatingId, t ? t.name : "Template");
                    }}
                  >
                    Confirm Deactivate
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
