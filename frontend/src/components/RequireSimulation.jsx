import { useEffect } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useSimulation } from "../context/SimulationContext";
import { pickResumableSimulation, toSimulationContext, useSimulationList } from "../hooks/useSimulationApi";
import LoadingScreen from "./LoadingScreen";

export default function RequireSimulation({ children }) {
  const { isAuthenticated, auth } = useAuth();
  const { simulation, setSimulation } = useSimulation();

  const needsRestore =
    isAuthenticated && auth?.role !== "admin" && (
      !simulation?.companyId || !simulation?.projectId || simulation?.studentId !== auth?.studentId
    );
  const { data: mySimulations, isLoading, error, refetch } = useSimulationList(needsRestore);

  useEffect(() => {
    if (!needsRestore || isLoading || error || !auth?.studentId) return;
    const resumable = pickResumableSimulation(mySimulations);
    if (resumable) setSimulation(toSimulationContext(resumable, auth.studentId));
    else setSimulation(null);
  }, [needsRestore, isLoading, error, mySimulations, auth?.studentId, setSimulation]);

  if (!isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  if (error && needsRestore) {
    return <div className="mx-auto max-w-xl p-6"><div className="card space-y-3 p-5 text-sm text-status-blocked" role="alert">Saved internships could not be loaded: {error.message}<button className="btn-ghost ml-2" onClick={() => refetch()}>Retry</button></div></div>;
  }

  if (auth?.role !== "admin") {
    if (!needsRestore && simulation?.companyId && simulation?.projectId) {
      return children;
    }
    // No cached context (e.g. fresh browser after logout): restore saved
    // progress from the backend before deciding the user has none.
    if (isLoading) {
      return <LoadingScreen label="Restoring internship progress" />;
    }
    const resumable = pickResumableSimulation(mySimulations);
    if (resumable) {
      return <LoadingScreen label="Restoring internship progress" />;
    }
    return <Navigate to="/" replace />;
  }

  return children;
}
