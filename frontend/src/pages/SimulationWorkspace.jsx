import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { useQueryClient } from "@tanstack/react-query";
import { useSimulation } from "../context/SimulationContext";
import { apiClient } from "../api/client";
import {
  useSimulation as useSimulationQuery,
  useCreateSimulation,
  useCurrentTask,
  useStartSimulationTask,
  useSubmitSimulationTask,
  useCompleteSimulationTask,
  useCurrentScenario,
  useSimulationProgress,
  useTaskEvents,
  useRespondToEvent,
} from "../hooks/useSimulationApi";
import { useCompany, useProject, useGenerateSimulation } from "../hooks/useApi";
import { useAuth } from "../context/AuthContext";
import LoadingScreen from "../components/LoadingScreen";
import ScenarioPanel from "../components/simulation/ScenarioPanel";
import TaskPanel from "../components/simulation/TaskPanel";
import WorkplaceEventPanel from "../components/simulation/WorkplaceEventPanel";
import ProgressIndicator from "../components/simulation/ProgressIndicator";
import SimulationChatPanel from "../components/simulation/SimulationChatPanel";
import AdaptiveInteractionPanel from "../components/simulation/AdaptiveInteractionPanel";

export default function SimulationWorkspace() {
  const { simulation, setSimulation } = useSimulation();
  const { auth } = useAuth();
  const simulationId = simulation?.id;
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  // Fetch simulation data
  const { data: simData, isLoading: simLoading, isError: simError, error: simLoadError } = useSimulationQuery(simulationId);
  const { data: scenarioData, isLoading: scenarioLoading, error: scenarioError } = useCurrentScenario(simulationId);
  const { data: currentTaskData, isLoading: taskLoading, isError: taskError, error: taskLoadError, isFetching: taskFetching } = useCurrentTask(simulationId);
  const { data: progressData, isLoading: progressLoading, error: progressError } = useSimulationProgress(simulationId);
  
  // Fetch company and selected project for workspace context
  const { data: company, error: companyError } = useCompany(simulation?.companyId);
  const { data: project, error: projectError } = useProject(simulation?.projectId);

  // Mutations
  const startTask = useStartSimulationTask(simulationId);
  const submitTask = useSubmitSimulationTask(simulationId);
  const completeTask = useCompleteSimulationTask(simulationId);
  const generateSimulation = useGenerateSimulation();
  
  // Events
  const { data: eventsData } = useTaskEvents(
    simulationId,
    currentTaskData?.task_id
  );
  const respondToEvent = useRespondToEvent(simulationId, currentTaskData?.task_id);

  // Local state
  const [showSubmissionModal, setShowSubmissionModal] = useState(false);
  const [actionError, setActionError] = useState("");
  const [simulationCompleted, setSimulationCompleted] = useState(false);

  // Check if simulation is completed
  useEffect(() => {
    if (simData?.status === "completed" || progressData?.overall_progress === 100) {
      setSimulationCompleted(true);
    }
  }, [simData, progressData]);

  // Handle simulation start
  const handleStartSimulation = async (overrideSimulationId) => {
    const idToUse = overrideSimulationId || simulationId;
    if (!idToUse) {
      setActionError("No simulation is available to start. Create an internship first.");
      return;
    }
    setActionError("");
    try {
      await apiClient.post(`/api/simulations/${idToUse}/start`);
      setSimulation(prev => ({ ...prev, id: idToUse, status: "in_progress" }));
      // Invalidate all relevant queries so the workspace fetches fresh data
      queryClient.invalidateQueries({ queryKey: ["simulation", idToUse] });
      queryClient.invalidateQueries({ queryKey: ["currentTask", idToUse] });
      queryClient.invalidateQueries({ queryKey: ["currentScenario", idToUse] });
      queryClient.invalidateQueries({ queryKey: ["simulationProgress", idToUse] });
      queryClient.invalidateQueries({ queryKey: ["board", simulation?.projectId] });
      // Navigate to the simulation workspace to show the current task
      navigate("/simulation");
    } catch (error) {
      setActionError(error.message || "The simulation could not be started. Retry when the service is available.");
    }
  };

  // Handle task start
  const handleStartTask = async (taskId) => {
    setActionError("");
    try {
      await startTask.mutateAsync(taskId);
    } catch (error) {
      const detail = error?.response?.data?.detail;
      setActionError(typeof detail === "string" ? detail : "Failed to start task. Please try again.");
    }
  };

  const createNewSimulation = async () => {
    const profile = await apiClient.get("/api/students/me");
    const recommendation = profile.data?.recommended_parameters;
    if (!recommendation || !auth?.studentId) {
      throw new Error("Complete your assessment before generating a new internship.");
    }
    const result = await generateSimulation.mutateAsync({
      student_id: auth.studentId,
      role: recommendation.role,
      technology_stack: recommendation.technology_stack,
      difficulty: recommendation.difficulty,
      company_type: recommendation.company_type,
    });
    // Switch active simulation only after generation succeeded. The old records
    // remain in the backend and are no longer the active context.
    setSimulation({
      companyId: result.company_id,
      projectId: result.project_id,
      id: result.simulation_id,
      title: result.title,
      description: result.description,
      role: result.role,
      difficulty: result.difficulty,
      duration_weeks: result.duration_weeks,
      total_tasks: result.task_count,
      status: "not_started",
      studentId: auth?.studentId || undefined,
    });
    setSimulationCompleted(false);
    navigate("/simulation");
  };

  // Handle task submission
  const handleSubmitTask = async (files, metadata) => {
    setActionError("");
    try {
      const result = await submitTask.mutateAsync({ taskId: currentTaskData.task_id, files, metadata });
      setShowSubmissionModal(false);
      if (result.version_id && simulation?.projectId) {
        try {
          const review = await apiClient.post(
            `/api/projects/${simulation.projectId}/reviews/versions/${result.version_id}`
          );
          // The engineering manager review is the second persisted review
          // step. It records the final decision and, on approval, completes
          // the task and advances the simulation.
          if (["approved", "changes_required", "rejected"].includes(review.data?.result)) {
            await apiClient.post(
              `/api/projects/${simulation.projectId}/reviews/versions/${result.version_id}/manager-review`
            );
          }
        } catch (reviewError) {
          const reviewDetail = reviewError?.response?.data?.detail;
          setActionError(`Work was submitted, but its review could not start: ${typeof reviewDetail === "string" ? reviewDetail : reviewError.message || "Please retry the review."}`);
        }
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["board", simulation?.projectId] }),
        queryClient.invalidateQueries({ queryKey: ["currentTask", simulationId] }),
        queryClient.invalidateQueries({ queryKey: ["currentScenario", simulationId] }),
        queryClient.invalidateQueries({ queryKey: ["simulationProgress", simulationId] }),
        queryClient.invalidateQueries({ queryKey: ["simulation", simulationId] }),
      ]);
      // Check if simulation completed
      if (result.simulation_completed || result.status === "simulation_completed") {
        setSimulationCompleted(true);
      }
      return result;
    } catch (error) {
      const detail = error?.response?.data?.detail;
      setActionError(typeof detail === "string" ? detail : error?.message || "Failed to submit task. Please try again.");
    }
  };

  // Handle task completion after an approved review: marks the task
  // COMPLETED and unlocks/progresses to the next task.
  const handleCompleteTask = async (taskId) => {
    setActionError("");
    try {
      const result = await completeTask.mutateAsync(taskId || currentTaskData.task_id);
      queryClient.invalidateQueries({ queryKey: ["currentTask", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["currentScenario", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["simulationProgress", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["simulation", simulationId] });
      queryClient.invalidateQueries({ queryKey: ["board", simulation?.projectId] });
      if (result.status === "simulation_completed") {
        setSimulationCompleted(true);
      }
    } catch (error) {
      console.error("Failed to complete task:", error);
      setActionError(error?.message || "Failed to complete task. Please try again.");
    }
  };

  const handleRetryReview = async (versionId) => {
    setActionError("");
    try {
      const review = await apiClient.post(
        `/api/projects/${simulation.projectId}/reviews/versions/${versionId}`
      );
      if (["approved", "changes_required", "rejected"].includes(review.data?.result)) {
        await apiClient.post(
          `/api/projects/${simulation.projectId}/reviews/versions/${versionId}/manager-review`
        );
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["currentTask", simulationId] }),
        queryClient.invalidateQueries({ queryKey: ["board", simulation?.projectId] }),
        queryClient.invalidateQueries({ queryKey: ["simulationProgress", simulationId] }),
      ]);
    } catch (error) {
      setActionError(error.message || "Technical review could not be retried. Your submission remains saved.");
    }
  };

  // Handle event response
  const handleEventResponse = async (eventId, response) => {
    try {
      await respondToEvent.mutateAsync({ eventId, response });
    } catch (error) {
      console.error("Failed to respond to event:", error);
      alert("Failed to record response. Please try again.");
    }
  };

  // If no simulation selected, show selection screen
  if (!simulationId) {
    return <SimulationSelectionScreen onStartSimulation={handleStartSimulation} error={actionError} />;
  }

  // Show loading while fetching
  if (simError) {
    return (
      <div className="mx-auto max-w-xl p-6">
        <motion.div
          initial={{ opacity: 0, scale: 0.97 }}
          animate={{ opacity: 1, scale: 1 }}
          className="card border-status-blocked/30 p-6 text-center text-sm text-status-blocked"
        >
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-status-blocked/15 text-xl shadow-[0_0_28px_-6px_rgba(239,91,91,0.7)]">⚠</div>
          {simLoadError?.message || "The simulation could not be loaded."}
          <button className="btn-ghost mt-3 block w-full" onClick={() => queryClient.invalidateQueries({ queryKey: ["simulation", simulationId] })}>Retry</button>
        </motion.div>
      </div>
    );
  }

  if (simLoading || scenarioLoading || taskLoading || progressLoading) {
    return <LoadingScreen label="Loading simulation workspace" />;
  }

  const queryError = simLoadError || taskLoadError || scenarioError || progressError;
  if (queryError) {
    return <div className="mx-auto max-w-xl p-6"><div className="card space-y-3 p-6 text-sm text-status-blocked" role="alert">
      <p>Simulation data could not be loaded: {queryError.message}</p>
      <button className="btn-ghost" onClick={() => {
        queryClient.invalidateQueries({ queryKey: ["simulation", simulationId] });
        queryClient.invalidateQueries({ queryKey: ["currentTask", simulationId] });
        queryClient.invalidateQueries({ queryKey: ["currentScenario", simulationId] });
        queryClient.invalidateQueries({ queryKey: ["simulationProgress", simulationId] });
      }}>Retry</button>
    </div></div>;
  }

  const sim = simData;
  const scenario = scenarioData;
  const currentTask = currentTaskData;
  const progress = progressData;
  const events = eventsData?.events || [];
  const activeEvent = events.find(e => e.status === "pending" || e.status === "acknowledged");

  // Simulation completed view
  if (simulationCompleted || sim?.status === "completed") {
    return (
      <SimulationCompletionScreen
        simulation={sim}
        progress={progress}
        onNewSimulation={createNewSimulation}
      />
    );
  }

  // Simulation not yet started — show the start screen
  if (sim?.status === "not_started") {
    return <SimulationSelectionScreen onStartSimulation={handleStartSimulation} error={actionError} />;
  }

  // No active task — determine the correct state from backend data
  if (!currentTask) {
    // If simulation is completed, show completion screen
    if (sim?.status === "completed" || simulationCompleted || progressData?.overall_progress === 100) {
      return (
        <SimulationCompletionScreen
          simulation={sim}
          progress={progress}
          onNewSimulation={createNewSimulation}
        />
      );
    }

    // If backend confirms scenario is completed, show scenario-complete message
    if (scenario?.status === "completed") {
      return (
        <div className="mx-auto max-w-6xl p-6 text-center">
          <motion.div
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            className="card mx-auto max-w-md p-8"
          >
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 16, delay: 0.1 }}
              className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-gradient-to-br from-status-completed to-accent-cyan shadow-[0_0_40px_-8px_rgba(62,214,149,0.9)]"
            >
              <svg className="h-8 w-8 text-ink" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
              </svg>
            </motion.div>
            <h2 className="mb-2 font-display text-xl font-bold">Scenario <span className="gradient-text">Complete!</span></h2>
            <p className="mb-4 text-sm text-text-muted">
              All tasks in this scenario have been completed. Preparing next scenario...
            </p>
            <div className="progress-track mx-auto h-1.5 max-w-[200px]">
              <div className="progress-fill" style={{ width: "100%" }} />
            </div>
          </motion.div>
        </div>
      );
    }

    const hasRemainingTasks = Number(sim?.total_tasks || 0) > Number(sim?.completed_tasks || 0);
    if (sim?.status === "in_progress" && hasRemainingTasks) {
      return (
        <div className="mx-auto max-w-6xl p-6 text-center">
          <div className="card mx-auto max-w-md space-y-3 p-8">
            <div className="relative mx-auto h-10 w-10">
              <div className="absolute inset-0 rounded-full border-2 border-brand-400/20" />
              <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-brand-300 animate-spin" style={{ animationDuration: "0.9s" }} />
            </div>
            <div className="animate-pulse-soft text-sm text-text-faint">Loading next task...</div>
          </div>
        </div>
      );
    }

    // Only show the wait state for a genuine temporary transition when there is
    // no remaining work and the backend cannot resolve a current task yet.
    if (taskError && !taskLoading && !taskFetching) {
      return (
        <div className="mx-auto max-w-6xl p-6 text-center">
          <motion.div
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            className="card mx-auto max-w-md p-8"
          >
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-brand-400/30 bg-brand-500/10 text-2xl text-brand-300 shadow-[0_0_32px_-10px_rgba(124,108,246,0.8)]">
              ◈
            </div>
            <h2 className="mb-2 font-display text-xl font-bold">Loading Next Task</h2>
            <p className="mb-5 text-sm text-text-muted">
              The simulation is resolving the next valid task for this sprint.
            </p>
            <button
              className="btn-primary w-full"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["currentTask", simulationId] })}
            >
              ↻ Retry
            </button>
          </motion.div>
        </div>
      );
    }

    return (
      <div className="mx-auto max-w-6xl p-6 text-center">
        <div className="card mx-auto max-w-md space-y-3 p-8">
          <div className="relative mx-auto h-10 w-10">
            <div className="absolute inset-0 rounded-full border-2 border-brand-400/20" />
            <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-brand-300 animate-spin" style={{ animationDuration: "0.9s" }} />
          </div>
          <div className="animate-pulse-soft text-sm text-text-faint">Loading next task...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-7xl space-y-4 p-4 min-h-[calc(100vh-80px)]">
      <header className="card flex flex-wrap items-center justify-between gap-3 px-5 py-3">
        <div className="min-w-0"><p className="eyebrow">{company?.name || "Internship workspace"}</p><h1 className="truncate font-display text-lg font-semibold">{sim?.role || project?.role || "Technical work"}</h1></div>
        <div className="flex flex-wrap items-center gap-2"><span className="chip capitalize">{sim?.difficulty || project?.difficulty || "Difficulty unavailable"}</span><span className="chip">Sprint {progress?.current_sprint_number || 1}</span></div>
      </header>
      {(companyError || projectError) && <div className="card border-status-blocked/30 p-3 text-sm text-status-blocked" role="alert">Internship context could not be loaded: {(companyError || projectError).message}. Task data remains available where authorized.</div>}
      {/* Progress Bar at top */}
      <div className="card overflow-hidden p-0!">
        <ProgressIndicator
          simulation={sim}
          progress={progress}
          scenario={scenario}
          currentTask={currentTask}
        />
      </div>
      {actionError && (
        <motion.div
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          className="card border-status-blocked/30 px-4 py-3 text-sm text-status-blocked"
          role="alert"
        >
          ⚠ {actionError}
        </motion.div>
      )}

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(340px,0.8fr)]">
        <main className="min-w-0">
          <TaskPanel
            task={currentTask}
            scenario={scenario}
            simulation={sim}
            project={project}
            onStart={handleStartTask}
            onSubmit={handleSubmitTask}
            onRetryReview={handleRetryReview}
            onComplete={handleCompleteTask}
            showSubmissionModal={showSubmissionModal}
            setShowSubmissionModal={setShowSubmissionModal}
            isStarting={startTask.isPending}
            isSubmitting={submitTask.isPending}
            isCompleting={completeTask.isPending}
            canStart={["todo", "changes_requested"].includes(currentTask.status)}
            canSubmit={currentTask.status === "in_progress"}
          />
        </main>

        <aside className="min-w-0 space-y-4">
          <div>
            <ScenarioPanel
              scenario={scenario}
              simulation={sim}
              progress={progress}
            />
          </div>
          <div className="h-[280px]">
            <WorkplaceEventPanel
              events={events}
              activeEvent={activeEvent}
              onRespond={handleEventResponse}
              isResponding={respondToEvent.isPending}
              suppressModal={showSubmissionModal}
            />
          </div>
          <div>
            <AdaptiveInteractionPanel simulationId={simulationId} />
          </div>
          <div className="h-[380px]">
            <SimulationChatPanel companyId={simulation?.companyId} />
          </div>
        </aside>
      </div>
    </div>
  );
}

/**
 * Simulation selection/start screen
 */
function SimulationSelectionScreen({ onStartSimulation, error }) {
  const { simulation, setSimulation } = useSimulation();
  const { auth } = useAuth();
  const createSimulation = useCreateSimulation();
  const [creating, setCreating] = useState(false);

  // If context has a simulation ID, we already have a simulation
  const hasSimulation = Boolean(simulation?.id);

  // If context only has companyId/projectId, create the simulation
  const handleCreateAndStart = async () => {
    if (!simulation?.companyId || !simulation?.projectId) return;
    setCreating(true);
    try {
      const result = await createSimulation.mutateAsync({
        companyId: simulation.companyId,
        projectId: simulation.projectId,
      });
      // Store full simulation data in context
      setSimulation({
        ...simulation,
        id: result.simulation_id,
        title: result.title,
        description: result.description,
        role: result.role,
        difficulty: result.difficulty,
        duration_weeks: result.duration_weeks,
        status: result.status,
        studentId: auth?.studentId || simulation?.studentId || undefined,
      });
      // Now start it — pass the new ID directly since context update is async
      await onStartSimulation(result.simulation_id);
    } catch (err) {
      console.error("Failed to create simulation:", err);
      alert("Failed to create simulation. Please try again.");
    } finally {
      setCreating(false);
    }
  };

  if (hasSimulation) {
    const facts = [
      ["💼 Role", simulation.role || "Internship"],
      ["🎯 Difficulty", simulation.difficulty || "—"],
      ["📅 Duration", simulation.duration_weeks ? `${simulation.duration_weeks} weeks` : "—"],
      ["🧩 Tasks", simulation.total_tasks || "?"],
    ];
    return (
      <div className="mx-auto max-w-3xl p-6 text-center">
        <motion.div
          initial={{ opacity: 0, y: 24, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
          className="card overflow-hidden p-8"
        >
          <div className="pointer-events-none absolute inset-x-0 top-0 h-32 bg-gradient-to-b from-brand-500/20 via-brand-500/[0.06] to-transparent" />
          <div className="relative">
            {error && <p className="mb-4 rounded-xl border border-status-blocked/30 bg-status-blocked/10 p-3 text-sm text-status-blocked" role="alert">{error}</p>}
            <motion.div
              initial={{ scale: 0.7, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 18, delay: 0.1 }}
              className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-3xl bg-gradient-to-br from-brand-500 via-brand-400 to-accent-cyan text-3xl shadow-[0_0_60px_-10px_rgba(124,108,246,1)]"
            >
              🚀
            </motion.div>
            <p className="eyebrow">Your workplace is ready</p>
            <h1 className="mt-2 font-display text-3xl font-bold">
              Ready to <span className="gradient-text">Start Internship</span>
            </h1>
            <p className="mx-auto mt-2 max-w-lg text-sm leading-relaxed text-text-muted">
              You're about to begin your <strong className="text-text">{simulation.role || "Internship"}</strong> simulation.
              Realistic workplace scenarios with tasks, deadlines, and unexpected events await.
            </p>

            <div className="mb-6 mt-6 space-y-3 text-left">
              <div className="rounded-2xl border border-brand-400/20 bg-gradient-to-br from-brand-500/[0.08] to-accent-cyan/[0.05] p-4">
                <p className="font-display font-semibold">{simulation.title}</p>
                <p className="mt-1 text-sm text-text-muted">{simulation.description}</p>
              </div>
              <div className="grid grid-cols-2 gap-2 text-sm">
                {facts.map(([k, v]) => (
                  <div key={k} className="rounded-xl border border-white/[0.06] bg-ink/40 px-3.5 py-2.5 text-left">
                    <p className="text-[11px] uppercase tracking-wider text-text-faint">{k}</p>
                    <p className="mt-0.5 font-semibold capitalize">{v}</p>
                  </div>
                ))}
              </div>
            </div>

            <motion.button
              whileHover={{ scale: 1.015 }}
              whileTap={{ scale: 0.985 }}
              className="btn-primary w-full py-3.5 text-lg"
              onClick={() => onStartSimulation(simulation.id)}
              disabled={simulation.status !== "not_started"}
            >
              ▶ Start Internship Simulation
            </motion.button>
            <div className="mt-4 flex items-center justify-center gap-4 text-[11px] text-text-faint">
              <span>⚡ Live AI manager</span><span>👥 AI teammates</span><span>📊 Real-time analytics</span>
            </div>
          </div>
        </motion.div>
      </div>
    );
  }

  // No simulation in context - show creation flow
  return (
    <div className="mx-auto max-w-3xl p-6 text-center">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="card p-10"
      >
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-white/[0.04] text-2xl">
          🛰
        </div>
        <h1 className="font-display text-2xl font-bold">No Active <span className="gradient-text">Simulation</span></h1>
        <p className="mx-auto mt-2 max-w-md text-sm text-text-muted">
          Complete the onboarding process to generate your internship simulation.
        </p>
        <div className="mx-auto mt-6 max-w-sm">
          {!simulation?.companyId ? (
            <a href="/" className="btn-primary w-full py-3">Go to Onboarding →</a>
          ) : (
            <button
              className="btn-primary w-full py-3"
              onClick={handleCreateAndStart}
              disabled={creating || createSimulation.isPending}
            >
              {creating || createSimulation.isPending ? "Creating Simulation..." : "Create & Start Simulation"}
            </button>
          )}
        </div>
      </motion.div>
    </div>
  );
}

/**
 * Simulation completion screen
 */
function SimulationCompletionScreen({ simulation, progress, onNewSimulation }) {
  const [confirmNew, setConfirmNew] = useState(false);
  const [newSimulationError, setNewSimulationError] = useState("");
  const [creatingNew, setCreatingNew] = useState(false);
  const confirmStartNew = async () => {
    setCreatingNew(true);
    setNewSimulationError("");
    try {
      await onNewSimulation();
    } catch (error) {
      setNewSimulationError(error?.response?.data?.detail || error.message || "Could not create a new simulation.");
    } finally {
      setCreatingNew(false);
    }
  };
  const durationMinutes = simulation?.started_at && simulation?.completed_at
    ? Math.round((new Date(simulation.completed_at) - new Date(simulation.started_at)) / 60000)
    : 0;

  const stats = [
    ["⏱ Total Duration", `${durationMinutes} min`],
    ["✓ Tasks Completed", `${progress?.completed_tasks || 0} / ${progress?.total_tasks || 0}`],
    ["◈ Progress", `${progress?.overall_progress || 100}%`],
    ["◷ Scenarios", `${progress?.scenarios?.length || 0}`],
  ];

  return (
    <div className="mx-auto max-w-3xl p-6">
      <motion.div
        initial={{ opacity: 0, scale: 0.94, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="card overflow-hidden p-8 text-center"
      >
        <div className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-gradient-to-b from-status-completed/15 via-status-completed/[0.05] to-transparent" />
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ type: "spring", stiffness: 220, damping: 14, delay: 0.15 }}
          className="relative mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full bg-gradient-to-br from-status-completed to-accent-cyan shadow-[0_0_60px_-8px_rgba(62,214,149,0.9)]"
        >
          <svg className="h-10 w-10 text-ink" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
          </svg>
          <span className="absolute inset-0 rounded-full border-2 border-status-completed/40 animate-ping" style={{ animationDuration: "2s" }} />
        </motion.div>

        <p className="eyebrow relative">★ Mission accomplished</p>
        <h1 className="relative mb-2 mt-1 font-display text-3xl font-bold sm:text-4xl">Internship <span className="gradient-text">Completed!</span></h1>
        <p className="relative mx-auto mb-6 max-w-md text-sm text-text-muted">
          Congratulations! You've successfully completed your <strong className="text-text">{simulation?.role}</strong> internship simulation.
        </p>

        <div className="relative mb-6 grid grid-cols-2 gap-3">
          {stats.map(([k, v], i) => (
            <motion.div
              key={k}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3 + i * 0.08 }}
              className="rounded-2xl border border-white/[0.07] bg-ink/50 p-4"
            >
              <p className="text-xs text-text-faint">{k}</p>
              <p className="mt-1 font-mono text-2xl font-bold text-text">{v}</p>
            </motion.div>
          ))}
        </div>

        <div className="relative space-y-3">
          <button className="btn-primary w-full py-3" onClick={() => setConfirmNew(true)}>
            + Start New Simulation
          </button>
          <a href="/dashboard" className="btn-ghost w-full py-3 block">
            View Dashboard →
          </a>
        </div>
      </motion.div>
      {confirmNew && <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/45 p-4" role="dialog" aria-modal="true" aria-labelledby="new-simulation-title"><div className="card w-full max-w-lg space-y-4 p-6"><h2 id="new-simulation-title" className="font-display text-xl font-semibold">Start a New Simulation?</h2><p className="text-sm leading-6 text-text-muted">This creates a separate internship simulation. Your current simulation and its progress will be preserved.</p>{newSimulationError && <p className="text-sm text-status-blocked" role="alert">{newSimulationError}</p>}<div className="flex justify-end gap-3"><button className="btn-ghost" onClick={() => { setConfirmNew(false); setNewSimulationError(""); }} disabled={creatingNew}>Cancel</button><button className="btn-primary" onClick={confirmStartNew} disabled={creatingNew}>{creatingNew ? "Creating..." : "Start New Simulation"}</button></div></div></div>}
    </div>
  );
}
