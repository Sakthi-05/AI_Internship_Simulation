import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useSimulation } from "../context/SimulationContext";

export default function RequireSimulation({ children }) {
  const { isAuthenticated, auth } = useAuth();
  const { simulation } = useSimulation();

  if (!isAuthenticated || (auth?.role !== "admin" && (!simulation?.companyId || !simulation?.projectId))) {
    return <Navigate to="/" replace />;
  }

  return children;
}
