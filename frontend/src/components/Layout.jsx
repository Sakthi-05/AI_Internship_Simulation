import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useAuth } from "../context/AuthContext";
import { useSimulation } from "../context/SimulationContext";
import { useCompany, useManager, useProject, useProjectState } from "../hooks/useApi";
import { useCompanyWebSocket } from "../hooks/useCompanyWebSocket";
import StatusStrip from "./StatusStrip";
import NotificationsBell from "./NotificationsBell";
import AmbientBackground from "./AmbientBackground";

const TABS = [
  { to: "/simulation", label: "Simulation" },
  { to: "/dashboard", label: "Dashboard" },
  { to: "/board", label: "Board" },
  { to: "/chat", label: "Chat" },
  { to: "/timeline", label: "Timeline" },
  { to: "/intelligence", label: "Intelligence" },
  { to: "/admin", label: "Admin" },
];

export default function Layout() {
  const { auth, logout } = useAuth();
  const { simulation, clearSimulation } = useSimulation();
  const location = useLocation();
  const navigate = useNavigate();
  useCompanyWebSocket(simulation?.companyId);

  const { data: company } = useCompany(simulation?.companyId);
  const { data: manager } = useManager(simulation?.companyId);
  const { data: project } = useProject(simulation?.projectId);
  const { data: state } = useProjectState(simulation?.projectId);

  const handleNewSimulation = () => {
    clearSimulation();
    navigate("/");
  };

  return (
    <div className="relative flex h-screen flex-col text-text">
      <AmbientBackground />
      <div className="relative z-10">
        <StatusStrip
          company={company}
          project={project}
          sprintNumber={state?.current_sprint_number}
          managerScore={state?.manager_satisfaction}
          managerName={manager?.name}
        />
      </div>

      <div className="relative z-10 flex items-center justify-between gap-3 border-b border-white/[0.06] bg-ink/60 px-4 backdrop-blur-xl">
        <nav className="flex gap-0.5">
          {TABS.filter((tab) => tab.to !== "/admin" || auth?.role === "admin").map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                `nav-tab ${isActive ? "nav-tab-active" : ""}`
              }
            >
              {({ isActive }) => (
                <span className="relative px-1 py-0.5">
                  {tab.label}
                  {isActive && (
                    <motion.span
                      layoutId="nav-active-glow"
                      className="absolute inset-x-0 -bottom-[11px] h-[2px] rounded-full bg-gradient-to-r from-brand-400 via-brand-300 to-accent-cyan shadow-[0_0_12px_2px_rgba(124,108,246,0.7)]"
                      transition={{ type: "spring", stiffness: 500, damping: 38 }}
                    />
                  )}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center gap-2.5 py-2 font-mono text-xs text-text-faint">
          <NotificationsBell companyId={simulation?.companyId} />
          <span className="hidden items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 sm:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-status-completed shadow-[0_0_8px_1px_rgba(62,214,149,0.9)] animate-pulse-soft" />
            {auth?.studentId}
          </span>
          <button
            onClick={handleNewSimulation}
            className="rounded-lg border border-white/10 px-2.5 py-1 text-text-muted transition-all duration-200 hover:-translate-y-px hover:border-accent-pink/50 hover:text-accent-pink hover:shadow-[0_8px_20px_-10px_rgba(244,113,181,0.7)]"
          >
            + new simulation
          </button>
          <button onClick={logout} className="rounded-lg px-2 py-1 text-text-faint transition-colors hover:text-text-muted">
            sign out
          </button>
        </div>
      </div>

      <div className="relative z-10 flex-1 overflow-auto">
        <AnimatePresence mode="wait">
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0, y: 14, scale: 0.998 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -10, scale: 0.999 }}
            transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
            className="h-full"
          >
            <Outlet />
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
