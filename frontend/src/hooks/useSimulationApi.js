import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { invalidateWorkspaceQueries } from "../lib/workspaceSync";

/**
 * Simulation API hooks for Member 2 workflow
 */

export function useCreateSimulation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ companyId, projectId }) => {
      const res = await apiClient.post(`/api/simulations`, {
        company_id: companyId,
        project_id: projectId,
      });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["simulation"] });
    },
  });
}

export function useSimulation(simulationId) {
  return useQuery({
    queryKey: ["simulation", simulationId],
    queryFn: async () => (await apiClient.get(`/api/simulations/${simulationId}`)).data,
    enabled: Boolean(simulationId),
    refetchInterval: 10000,
  });
}

export function useStartSimulation(simulationId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const res = await apiClient.post(`/api/simulations/${simulationId}/start`);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["simulation", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["simulationProgress", simulationId] });
    },
  });
}

export function useSimulationProgress(simulationId) {
  return useQuery({
    queryKey: ["simulationProgress", simulationId],
    queryFn: async () => (await apiClient.get(`/api/simulations/${simulationId}/progress`)).data,
    enabled: Boolean(simulationId),
    refetchInterval: 10000,
  });
}

export function useCurrentScenario(simulationId) {
  return useQuery({
    queryKey: ["currentScenario", simulationId],
    queryFn: async () => (await apiClient.get(`/api/simulations/${simulationId}/scenario`)).data,
    enabled: Boolean(simulationId),
    refetchInterval: 10000,
  });
}

export function useSimulationTasks(simulationId) {
  return useQuery({
    queryKey: ["simulationTasks", simulationId],
    queryFn: async () => (await apiClient.get(`/api/simulations/${simulationId}/tasks`)).data,
    enabled: Boolean(simulationId),
    refetchInterval: 10000,
  });
}

export function useCurrentTask(simulationId) {
  return useQuery({
    queryKey: ["currentTask", simulationId],
    queryFn: async () => (await apiClient.get(`/api/simulations/${simulationId}/tasks/current`)).data,
    enabled: Boolean(simulationId),
    refetchInterval: 5000, // More frequent for timer
  });
}

export function useStartSimulationTask(simulationId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (taskId) => {
      const res = await apiClient.post(`/api/simulations/${simulationId}/tasks/${taskId}/start`);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["currentTask", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["currentScenario", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["simulation", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["taskEvents", simulationId] });
    },
  });
}

export function useSubmitSimulationTask(simulationId) {
  return useMutation({
    mutationFn: async ({ taskId, files, metadata }) => {
      const formData = new FormData();
      if (files) {
        files.forEach((file) => formData.append("files", file));
      }
      if (metadata) {
        const source = typeof metadata.code === "string" ? metadata.code : "";
        if (source) {
          console.info("Submitting editor source", {
            taskId,
            sourceChars: source.length,
            sourceBytes: new TextEncoder().encode(source).length,
            expectedEndMarkerPresent: metadata.expected_end_marker
              ? source.includes(metadata.expected_end_marker)
              : null,
          });
        }
        formData.append("metadata", JSON.stringify(metadata));
      }
      // Let the browser attach the multipart boundary required by FastAPI.
      const res = await apiClient.post(`/api/simulations/${simulationId}/tasks/${taskId}/submit`, formData);
      return res.data;
    },
  });
}

export function useCompleteSimulationTask(simulationId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (taskId) => {
      const res = await apiClient.post(`/api/simulations/${simulationId}/tasks/${taskId}/complete`);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["currentTask", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["currentScenario", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["simulation", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["simulationProgress", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["taskEvents", simulationId] });
    },
  });
}

export function useTaskEvents(simulationId, taskId) {
  return useQuery({
    queryKey: ["taskEvents", simulationId, taskId],
    queryFn: async () => (await apiClient.get(`/api/simulations/${simulationId}/tasks/${taskId}/events`)).data,
    enabled: Boolean(simulationId) && Boolean(taskId),
    refetchInterval: 5000,
  });
}

function refreshWorkplace(queryClient) {
  return invalidateWorkspaceQueries(queryClient);
}

export function useRespondToEvent(simulationId, taskId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ eventId, response }) => {
      const formData = new URLSearchParams();
      formData.append("response", response);
      const res = await apiClient.post(
        `/api/simulations/${simulationId}/tasks/${taskId}/events/${eventId}/respond`,
        formData,
        { headers: { "Content-Type": "application/x-www-form-urlencoded" } }
      );
      return res.data;
    },
    onSuccess: () => refreshWorkplace(queryClient),
  });
}

export function useAcknowledgeEvent(simulationId, taskId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (eventId) => {
      const res = await apiClient.post(
        `/api/simulations/${simulationId}/tasks/${taskId}/events/${eventId}/acknowledge`
      );
      return res.data;
    },
    onSuccess: () => refreshWorkplace(queryClient),
  });
}

export function useRecordWorkplaceAction(simulationId, taskId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ eventId, detail }) => {
      const res = await apiClient.post(
        `/api/simulations/${simulationId}/tasks/${taskId}/events/${eventId}/actions`,
        { action: "adapted_work", detail }
      );
      return res.data;
    },
    onSuccess: () => refreshWorkplace(queryClient),
  });
}

export function useSimulationList(enabled = true) {
  return useQuery({
    queryKey: ["simulationList"],
    queryFn: async () => (await apiClient.get("/api/simulations/mine")).data,
    enabled: Boolean(enabled),
    staleTime: 5000,
  });
}

export function pickResumableSimulation(simulations) {
  const list = Array.isArray(simulations) ? simulations : [];
  // Prefer an in-progress simulation, then a not-started one. Completed or
  // abandoned simulations are not resumable progress.
  const byStatus = (status) => list.find((s) => s?.status === status);
  return (
    byStatus("in_progress") ||
    byStatus("not_started") ||
    null
  );
}

export function toSimulationContext(entry, studentId) {
  if (!entry) return null;
  return {
    id: entry.simulation_id || entry.id,
    companyId: entry.company_id || entry.companyId,
    projectId: entry.project_id || entry.projectId,
    title: entry.title,
    description: entry.description,
    role: entry.role,
    difficulty: entry.difficulty,
    duration_weeks: entry.duration_weeks,
    total_tasks: entry.task_count ?? entry.total_tasks,
    status: entry.status || "not_started",
    ...(studentId ? { studentId } : {}),
  };
}
