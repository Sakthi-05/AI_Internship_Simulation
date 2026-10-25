import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useAuth } from "../context/AuthContext";
import { useSimulation } from "../context/SimulationContext";
import { useCompany, useManager, useProject, useProjectState } from "../hooks/useApi";
import { useCompanyWebSocket } from "../hooks/useCompanyWebSocket";
import StatusStrip from "./StatusStrip";
import NotificationsBell from "./NotificationsBell";

const TABS = [
  { to: "/dashboard", label: "Overview", icon: "▦" },
  { to: "/simulation", label: "Simulation", icon: "◉" },
  { to: "/board", label: "Tasks", icon: "☷" },
  { to: "/chat", label: "Messages", icon: "◌" },
  { to: "/timeline", label: "Timeline", icon: "◷" },
  { to: "/intelligence", label: "Reports & skills", icon: "⌁" },
  { to: "/admin", label: "Faculty admin", icon: "⚙" },
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
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="brand-lockup"><span className="brand-mark">AI</span><span><strong>AI Internship</strong><small>Simulation platform</small></span></div>
        <p className="sidebar-label">WORKSPACE</p>
        <nav className="sidebar-nav" aria-label="Main navigation">
          {TABS.filter((tab) => tab.to !== "/admin" || auth?.role === "admin").map((tab) => (
            <NavLink key={tab.to} to={tab.to} className={({ isActive }) => `sidebar-link ${isActive ? "sidebar-link-active" : ""}`}>
              <span className="sidebar-icon" aria-hidden="true">{tab.icon}</span><span>{tab.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="workspace-card"><span className="workspace-dot"/><div><strong>{company?.name || "Your workspace"}</strong><small>{project?.title || "Active internship"}</small></div></div>
          <div className="profile-row"><span className="profile-avatar">{String(auth?.studentId || "U").slice(0, 1).toUpperCase()}</span><span className="profile-copy"><strong>{auth?.studentId || "Learner"}</strong><small>{auth?.role === "admin" ? "Faculty account" : "Student account"}</small></span><button className="signout-button" onClick={logout} aria-label="Sign out" title="Sign out">↗</button></div>
        </div>
      </aside>
      <div className="app-main">
        <header className="app-topbar">
          <div className="mobile-brand"><span className="brand-mark">AI</span><strong>AI Internship</strong></div>
          <div className="topbar-context"><StatusStrip company={company} project={project} sprintNumber={state?.current_sprint_number} managerScore={state?.manager_satisfaction} managerName={manager?.name}/></div>
          <div className="topbar-actions"><NotificationsBell companyId={simulation?.companyId}/><button onClick={handleNewSimulation} className="btn-secondary">+ New simulation</button></div>
        </header>
        <nav className="mobile-nav" aria-label="Main navigation">{TABS.filter((tab) => tab.to !== "/admin" || auth?.role === "admin").map((tab) => <NavLink key={tab.to} to={tab.to} className={({ isActive }) => `mobile-nav-link ${isActive ? "mobile-nav-active" : ""}`}>{tab.label}</NavLink>)}</nav>
        <main className="app-content">
          <AnimatePresence mode="wait"><motion.div key={location.pathname} initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.16 }} className="app-page"><Outlet/></motion.div></AnimatePresence>
        </main>
      </div>
    </div>
  );
}
