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
    isAuthenticated && auth?.role !== "admin" && (!simulation?.companyId || !simulation?.projectId);
  const { data: mySimulations, isLoading } = useSimulationList(needsRestore);

  useEffect(() => {
    if (!needsRestore || isLoading || !auth?.studentId) return;
    const resumable = pickResumableSimulation(mySimulations);
    if (resumable) {
      setSimulation(toSimulationContext(resumable, auth.studentId));
    }
  }, [needsRestore, isLoading, mySimulations, auth?.studentId, setSimulation]);

  if (!isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  if (auth?.role !== "admin") {
    if (simulation?.companyId && simulation?.projectId) {
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
