import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function RequireAdmin({ children }) {
  const { auth } = useAuth();
  return ["admin", "administrator"].includes(auth?.role)
    ? children
    : <Navigate to="/dashboard" replace />;
}
