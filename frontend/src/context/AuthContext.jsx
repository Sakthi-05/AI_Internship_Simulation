import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { apiClient, setAuthFailureHandler, setAuthToken } from "../api/client";

const AuthContext = createContext(null);

const STORAGE_KEY = "aiwse:auth";

function persist(next) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });

  useEffect(() => {
    setAuthToken(auth?.token || null);
  }, [auth]);

  useEffect(() => {
    setAuthFailureHandler((error) => {
      if (error?.response?.status !== 401) {
        return;
      }

      localStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem("aiwse:simulation");
      setAuth(null);
    });

    return () => {
      setAuthFailureHandler(null);
    };
  }, []);

  const applySession = useCallback((data) => {
    const next = persist({
      token: data.access_token,
      studentId: data.student_id,
      role: data.role,
      email: data.email || null,
      fullName: data.full_name || null,
    });
    setAuth(next);
    return next;
  }, []);

  const login = useCallback(async (email, password) => {
    const res = await apiClient.post("/api/auth/login", { email, password });
    return applySession(res.data);
  }, [applySession]);

  const register = useCallback(async ({ email, password, fullName }) => {
    const res = await apiClient.post("/api/auth/register", {
      email,
      password,
      full_name: fullName,
    });
    return applySession(res.data);
  }, [applySession]);

  const loginDev = useCallback(async (studentId, role = "student") => {
    const res = await apiClient.post("/api/dev/token", { student_id: studentId, role });
    return applySession(res.data);
  }, [applySession]);

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem("aiwse:simulation");
    setAuth(null);
  }, []);

  return (
    <AuthContext.Provider value={{ auth, login, register, loginDev, logout, isAuthenticated: Boolean(auth?.token) }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
