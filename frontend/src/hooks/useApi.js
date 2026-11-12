import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { invalidateWorkspaceQueries } from "../lib/workspaceSync";

export function useGenerateSimulation() {
  return useMutation({
    mutationFn: async (payload) => {
      const res = await apiClient.post("/api/generate", payload);
      return res.data;
    },
  });
}

export function useCompany(companyId) {
  return useQuery({
    queryKey: ["company", companyId],
    queryFn: async () => (await apiClient.get(`/api/companies/${companyId}`)).data,
    enabled: Boolean(companyId),
  });
}

export function useManager(companyId) {
  return useQuery({
    queryKey: ["manager", companyId],
    queryFn: async () => (await apiClient.get(`/api/companies/${companyId}/manager`)).data,
    enabled: Boolean(companyId),
  });
}

export function useProject(projectId) {
  return useQuery({
    queryKey: ["project", projectId],
    queryFn: async () => (await apiClient.get(`/api/projects/${projectId}`)).data,
    enabled: Boolean(projectId),
  });
}

export function useProjectState(projectId) {
  return useQuery({
    queryKey: ["projectState", projectId],
    queryFn: async () => (await apiClient.get(`/api/projects/${projectId}/state`)).data,
    enabled: Boolean(projectId),
    refetchInterval: 30000,
  });
}

export function useSprints(projectId) {
  return useQuery({
    queryKey: ["sprints", projectId],
    queryFn: async () => (await apiClient.get(`/api/projects/${projectId}/sprints`)).data,
    enabled: Boolean(projectId),
  });
}

export function useBoard(projectId, simulationId) {
  return useQuery({
    queryKey: ["board", projectId, simulationId],
    queryFn: async () => {
      const params = simulationId ? `?simulation_id=${simulationId}` : "";
      return (await apiClient.get(`/api/projects/${projectId}/board${params}`)).data;
    },
    enabled: Boolean(projectId),
    refetchInterval: 8000,
  });
}

export function useUpdateTaskStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ taskId, ...body }) => {
      const res = await apiClient.patch(`/api/tasks/${taskId}/status`, body);
      return res.data;
    },
    onSuccess: () => invalidateWorkspaceQueries(queryClient),
  });
}

export function useReportBug(projectId) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ taskId, description }) => {
      const res = await apiClient.post(`/api/tasks/${taskId}/report-bug`, { description });
      return res.data;
    },
    onSuccess: () => {
      return Promise.all([
        invalidateWorkspaceQueries(queryClient),
        queryClient.invalidateQueries({ queryKey: ["projectState", projectId] }),
      ]);
    },
  });
}

export function useTeam(companyId) {
  return useQuery({
    queryKey: ["team", companyId],
    queryFn: async () => (await apiClient.get(`/api/companies/${companyId}/team`)).data,
    enabled: Boolean(companyId),
  });
}

export function useManagerChat(companyId) {
  const queryClient = useQueryClient();
  const history = useQuery({
    queryKey: ["chat", "manager", companyId],
    queryFn: async () => (await apiClient.get(`/api/companies/${companyId}/chat/manager`)).data,
    enabled: Boolean(companyId),
    // Polling fallback so proactive manager check-ins appear over time even
    // when the WebSocket feed drops. WS pushes invalidate ["chat"] immediately.
    refetchInterval: 15000,
  });
  const send = useMutation({
    mutationFn: async ({ content, idempotency_key }) =>
      (await apiClient.post(`/api/companies/${companyId}/chat/manager`, { content, idempotency_key })).data,
    onSuccess: () => invalidateWorkspaceQueries(queryClient),
  });
  return { history, send };
}

export function useTeamChat(companyId, teammateId) {
  const queryClient = useQueryClient();
  const history = useQuery({
    queryKey: ["chat", "team", companyId, teammateId],
    queryFn: async () => (await apiClient.get(`/api/companies/${companyId}/chat/team/${teammateId}`)).data,
    enabled: Boolean(companyId) && Boolean(teammateId),
    // Same polling fallback as the manager thread for teammate check-ins.
    refetchInterval: 15000,
  });
  const send = useMutation({
    mutationFn: async ({ content, idempotency_key }) =>
      (await apiClient.post(`/api/companies/${companyId}/chat/team/${teammateId}`, { content, idempotency_key })).data,
    onSuccess: () => invalidateWorkspaceQueries(queryClient),
  });
  return { history, send };
}

export function useNotifications(companyId) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["notifications", companyId],
    queryFn: async () => (await apiClient.get(`/api/companies/${companyId}/notifications`)).data,
    enabled: Boolean(companyId),
    refetchInterval: 10000,
  });
  const markRead = useMutation({
    mutationFn: async (notificationId) =>
      (await apiClient.patch(`/api/companies/${companyId}/notifications/${notificationId}/read`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications", companyId] }),
  });
  return { ...query, markRead };
}

export function useEvents(projectId) {
  return useQuery({
    queryKey: ["events", projectId],
    queryFn: async () => (await apiClient.get(`/api/projects/${projectId}/events`)).data,
    enabled: Boolean(projectId),
  });
}

export function useActivity(companyId) {
  return useQuery({
    queryKey: ["activity", companyId],
    queryFn: async () => (await apiClient.get(`/api/companies/${companyId}/activity`)).data,
    enabled: Boolean(companyId),
  });
}

export function useMeetings(projectId) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["meetings", projectId],
    queryFn: async () => (await apiClient.get(`/api/projects/${projectId}/meetings`)).data,
    enabled: Boolean(projectId),
  });
  const complete = useMutation({
    mutationFn: async ({ meetingId, ...body }) =>
      (await apiClient.patch(`/api/projects/${projectId}/meetings/${meetingId}/complete`, body)).data,
    onSuccess: () => Promise.all([
      queryClient.invalidateQueries({ queryKey: ["meetings", projectId] }),
      queryClient.invalidateQueries({ queryKey: ["timeline", projectId] }),
    ]),
  });
  const respond = useMutation({
    mutationFn: async ({ meetingId, ...body }) =>
      (await apiClient.post(`/api/projects/${projectId}/meetings/${meetingId}/respond`, body)).data,
    onSuccess: () => Promise.all([
      queryClient.invalidateQueries({ queryKey: ["meetings", projectId] }),
      queryClient.invalidateQueries({ queryKey: ["timeline", projectId] }),
    ]),
  });
  const update = useMutation({
    mutationFn: async ({ meetingId, ...body }) =>
      (await apiClient.patch(`/api/projects/${projectId}/meetings/${meetingId}`, body)).data,
    onSuccess: () => Promise.all([
      queryClient.invalidateQueries({ queryKey: ["meetings", projectId] }),
      queryClient.invalidateQueries({ queryKey: ["timeline", projectId] }),
    ]),
  });
  return { ...query, complete, respond, update };
}

export function useTimeline(projectId) {
  return useQuery({
    queryKey: ["timeline", projectId],
    queryFn: async () => (await apiClient.get(`/api/projects/${projectId}/timeline`)).data,
    enabled: Boolean(projectId),
    refetchInterval: 8000,
  });
}
