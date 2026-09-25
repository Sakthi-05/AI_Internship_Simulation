import { createContext, useContext, useState, useCallback } from "react";

const SimulationContext = createContext(null);
const STORAGE_KEY = "aiwse:simulation";

export function SimulationProvider({ children }) {
  const [simulation, setSimulationState] = useState(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });

  const setSimulation = useCallback((data) => {
    setSimulationState((current) => {
      const next = typeof data === "function" ? data(current) : data;
      if (next) localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      else localStorage.removeItem(STORAGE_KEY);
      return next;
    });
  }, []);

  const clearSimulation = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setSimulationState(null);
  }, []);

  return (
    <SimulationContext.Provider value={{ simulation, setSimulation, clearSimulation }}>
      {children}
    </SimulationContext.Provider>
  );
}

export function useSimulation() {
  const ctx = useContext(SimulationContext);
  if (!ctx) throw new Error("useSimulation must be used within SimulationProvider");
  return ctx;
}
