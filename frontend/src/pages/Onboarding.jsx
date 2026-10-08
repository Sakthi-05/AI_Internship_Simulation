import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { useAuth } from "../context/AuthContext";
import { useSimulation } from "../context/SimulationContext";
import { useGenerateSimulation } from "../hooks/useApi";
import { pickResumableSimulation, toSimulationContext, useSimulationList } from "../hooks/useSimulationApi";
import LoadingScreen from "../components/LoadingScreen";
import AmbientBackground from "../components/AmbientBackground";
import { apiClient } from "../api/client";

const STEP_ORDER = ["auth", "profile", "skills", "assessment", "ready"];
const STEP_LABELS = {
  auth: "Sign in",
  profile: "Profile",
  skills: "Skills",
  assessment: "Assessment",
  ready: "Launch",
};

// Shown instead of a raw transport failure after assessment completion.
// The assessment itself is scored and saved synchronously, so a failure here
// is non-blocking: results stay visible and the internship can start.
const AI_UNAVAILABLE_MESSAGE = "AI service is temporarily unavailable. Your results are saved.";

function isTransportFailure(err) {
  if (err?.response) return false;
  const msg = err?.message || "";
  return msg === "Network Error" || /network|timeout|timed out|failed to fetch|econn/i.test(msg);
}

function Stepper({ step }) {
  const activeIdx = STEP_ORDER.indexOf(step);
  return (
    <div className="mt-6 flex items-center gap-1.5">
      {STEP_ORDER.map((s, i) => {
        const done = i < activeIdx;
        const active = i === activeIdx;
        return (
          <div key={s} className="flex flex-1 items-center gap-1.5 last:flex-none">
            <div className="flex flex-col gap-1.5">
              <motion.div
                initial={false}
                animate={done || active ? { opacity: 1 } : { opacity: 0.45 }}
                className={`h-1.5 w-10 rounded-full sm:w-14 ${
                  done
                    ? "bg-status-completed shadow-[0_0_10px_rgba(62,214,149,0.7)]"
                    : active
                      ? "bg-gradient-to-r from-brand-500 to-accent-cyan shadow-[0_0_12px_rgba(124,108,246,0.8)]"
                      : "bg-white/10"
                }`}
              />
              <span className={`hidden text-[10px] font-medium uppercase tracking-wider sm:block ${active ? "text-text" : "text-text-faint"}`}>
                {STEP_LABELS[s]}
              </span>
            </div>
            {i < STEP_ORDER.length - 1 && <div className="mb-5 hidden h-px flex-1 bg-white/5 sm:block" />}
          </div>
        );
      })}
    </div>
  );
}

const COMPANY_TYPES = [
  ["startup", "Startup"],
  ["product_company", "Product Company"],
  ["mnc", "MNC"],
  ["healthcare", "Healthcare"],
  ["banking", "Banking"],
  ["ecommerce", "E-Commerce"],
  ["education", "Education"],
];

const ROLES = [
  "Backend Developer",
  "Frontend Developer",
  "Full Stack Developer",
  "AI Engineer",
  "Data Engineer",
  "Cybersecurity",
  "DevOps Engineer",
  "Mobile Developer",
];

export default function Onboarding() {
  const { auth, login, register, loginDev, logout } = useAuth();
  const { simulation, setSimulation, clearSimulation, unloadSimulation } = useSimulation();
  const navigate = useNavigate();
  const generateMutation = useGenerateSimulation();

  // Existing internship progress for this student (source of truth after login).
  const simsEnabled = Boolean(auth?.token) && auth?.role !== "admin";
  const { data: mySimulations, isLoading: simsLoading } = useSimulationList(simsEnabled);
  const resumable = pickResumableSimulation(mySimulations);

  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [studentId, setStudentId] = useState("student_001");
  const [loginRole, setLoginRole] = useState("student");
  const [authError, setAuthError] = useState(null);
  const [busy, setBusy] = useState(false);

  const [profile, setProfile] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const [selectedSkills, setSelectedSkills] = useState({});
  const [attempt, setAttempt] = useState(null);
  const [answers, setAnswers] = useState({});
  const [formError, setFormError] = useState(null);
  const [notice, setNotice] = useState(null);

  const [college, setCollege] = useState("");
  const [degree, setDegree] = useState("");
  const [year, setYear] = useState("3");
  const [careerGoal, setCareerGoal] = useState("");
  const [role, setRole] = useState("Backend Developer");
  const [domain, setDomain] = useState("startup");

  useEffect(() => {
    if (!auth?.token || !auth?.studentId) {
      setProfile(null);
      setAttempt(null);
      setAnswers({});
      setSelectedSkills({});
      setFullName("");
      setCollege("");
      setDegree("");
      setCareerGoal("");
      // Preserve the cached simulation across logout so the next login can
      // resume it; only drop it from memory.
      if (typeof unloadSimulation === "function") unloadSimulation();
      else clearSimulation();
      return;
    }
    if (auth.role === "admin") return;
    let cancelled = false;
    apiClient.get("/api/students/me").then((res) => {
      if (cancelled) return;
      const data = res.data;
      setProfile(data);
      setFullName(data.full_name || auth.fullName || "");
      setCollege(data.college || "");
      setDegree(data.degree || "");
      setYear(String(data.year_of_study || 3));
      setCareerGoal(data.career_goal || "");
      setRole(data.role || "Backend Developer");
      setDomain(data.domain || "startup");
      const ratings = {};
      (data.skill_ratings || []).forEach((item) => {
        ratings[item.name] = item.self_rating;
      });
      setSelectedSkills(ratings);
    }).catch((err) => setFormError(err.message));
    apiClient.get("/api/skills").then((res) => {
      if (!cancelled) setCatalog(res.data.skills || []);
    }).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [auth, clearSimulation, unloadSimulation]);

  // Restore saved progress into context once the backend list resolves.
  // - Returning user (resumable found): seed context so "Resume" lands
  //   directly on saved progress at /simulation.
  // - New user (empty list): drop any stale cache from another student.
  useEffect(() => {
    if (!simsEnabled || simsLoading || !auth?.studentId) return;
    if (resumable) {
      const next = toSimulationContext(resumable, auth.studentId);
      const same = simulation?.id === next.id;
      if (!same) setSimulation(next);
    } else if (Array.isArray(mySimulations) && mySimulations.length === 0 && simulation) {
      if (!simulation.studentId || simulation.studentId !== auth.studentId) {
        clearSimulation();
      }
    }
    // Intentionally not depending on `simulation` identity to avoid loops;
    // identity is compared via simulation.id above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simsEnabled, simsLoading, resumable, mySimulations, auth?.studentId, setSimulation, clearSimulation]);

  const handleSignOut = () => {
    logout();
    // Keep the localStorage cache; only unload from memory so the next
    // login on this browser can resume the same internship.
    if (typeof unloadSimulation === "function") unloadSimulation();
    else clearSimulation();
    setProfile(null);
    setAttempt(null);
    setAnswers({});
    setSelectedSkills({});
    setFormError(null);
    setNotice(null);
    setAuthError(null);
    setMode("login");
    navigate("/", { replace: true });
  };

  const handleAuth = async (event) => {
    event.preventDefault();
    setAuthError(null);
    setBusy(true);
    try {
      const session = mode === "register"
        ? await register({ email, password, fullName })
        : await login(email, password);
      if (session.role === "admin") navigate("/admin");
    } catch (err) {
      setAuthError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const handleDev = async (event) => {
    event.preventDefault();
    setAuthError(null);
    setBusy(true);
    try {
      const session = await loginDev(studentId.trim(), loginRole);
      if (session.role === "admin") navigate("/admin");
    } catch (err) {
      setAuthError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const saveProfile = async (event) => {
    event.preventDefault();
    setFormError(null);
    setBusy(true);
    try {
      const res = await apiClient.put("/api/students/me", {
        full_name: fullName,
        domain,
        role,
        college,
        degree,
        year_of_study: Number(year),
        career_goal: careerGoal,
      });
      setProfile(res.data);
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const saveSkills = async (event) => {
    event.preventDefault();
    const skills = Object.entries(selectedSkills).map(([name, self_rating]) => ({ name, self_rating }));
    if (!skills.length) {
      setFormError("Select at least one skill.");
      return;
    }
    setFormError(null);
    setBusy(true);
    try {
      await apiClient.put("/api/students/me/skills", { skills });
      const res = await apiClient.get("/api/students/me");
      setProfile(res.data);
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const beginAssessment = async () => {
    setFormError(null);
    setBusy(true);
    try {
      const res = await apiClient.post("/api/assessments/start");
      setAttempt(res.data);
      setAnswers({});
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const submitAssessment = async (event) => {
    event.preventDefault();
    const payload = (attempt?.questions || []).map((question) => ({
      question_id: question.id,
      response: (answers[question.id] || "").trim(),
    }));
    if (payload.some((item) => !item.response)) {
      setFormError("Answer every question before submitting.");
      return;
    }
    setFormError(null);
    setNotice(null);
    setBusy(true);
    try {
      const submitRes = await apiClient.post(`/api/assessments/${attempt.id}/submit`, { answers: payload });
      // The submit response already carries the scored profile, so a
      // transient failure on the refresh below must not hide the results.
      // Merge over the current profile to keep view-only keys (assessed,
      // skill_ratings, profile_complete) that the submit payload omits.
      let nextProfile = { ...(profile || {}), ...(submitRes.data?.profile || {}), assessed: true };
      try {
        const res = await apiClient.get("/api/students/me");
        nextProfile = res.data;
      } catch (refreshErr) {
        if (!isTransportFailure(refreshErr)) throw refreshErr;
        setNotice(AI_UNAVAILABLE_MESSAGE);
      }
      setProfile(nextProfile);
      setAttempt(null);
    } catch (err) {
      // A transport failure can land after the server already saved the
      // assessment — re-read the saved state instead of dropping results.
      if (isTransportFailure(err)) {
        try {
          const res = await apiClient.get("/api/students/me");
          if (res.data?.assessed) {
            setProfile(res.data);
            setAttempt(null);
            setNotice(AI_UNAVAILABLE_MESSAGE);
            return;
          }
        } catch {
          // Fall through to the friendly message below.
        }
        setNotice(AI_UNAVAILABLE_MESSAGE);
        return;
      }
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const startInternship = async (event) => {
    event?.preventDefault?.();
    if (!auth?.token || !auth?.studentId) {
      setAuthError("Your session has expired. Sign in again to start the internship.");
      setProfile(null);
      setAttempt(null);
      setAnswers({});
      clearSimulation();
      navigate("/", { replace: true });
      return;
    }
    const recommendation = profile?.recommended_parameters;
    if (!recommendation) return;
    setFormError(null);
    try {
      const generationPayload = {
        student_id: auth.studentId,
        role: recommendation.role,
        technology_stack: recommendation.technology_stack,
        difficulty: recommendation.difficulty,
        company_type: recommendation.company_type,
      };
      const storageKey = `internship-generation:${auth.studentId}`;
      const fingerprint = JSON.stringify(generationPayload);
      let pendingRequest;
      try { pendingRequest = JSON.parse(sessionStorage.getItem(storageKey) || "null"); } catch { pendingRequest = null; }
      if (!pendingRequest || pendingRequest.fingerprint !== fingerprint || !pendingRequest.requestId) {
        pendingRequest = { fingerprint, requestId: crypto.randomUUID() };
        sessionStorage.setItem(storageKey, JSON.stringify(pendingRequest));
      }
      const result = await generateMutation.mutateAsync({
        ...generationPayload,
        generation_request_id: pendingRequest.requestId,
      });
      sessionStorage.removeItem(storageKey);
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
        studentId: auth.studentId,
      });
      navigate("/simulation");
    } catch (err) {
      setFormError(err.message);
    }
  };

  const resumeInternship = (event) => {
    event?.preventDefault?.();
    const target = resumable || (simulation?.id ? {
      simulation_id: simulation.id,
      company_id: simulation.companyId,
      project_id: simulation.projectId,
      title: simulation.title,
      description: simulation.description,
      role: simulation.role,
      difficulty: simulation.difficulty,
      duration_weeks: simulation.duration_weeks,
      status: simulation.status,
    } : null);
    if (!target) return;
    setSimulation(toSimulationContext(target, auth?.studentId));
    // Takes the returning user directly to their saved progress; the
    // workspace loads current task/scenario/progress from the backend.
    navigate("/simulation");
  };

  if (generateMutation.isPending || (auth?.token && auth.role !== "admin" && !profile)) {
    return <LoadingScreen />;
  }

  const step = !auth?.token || !auth?.studentId ? "auth" : !profile ? "profile" : !profile.profile_complete ? "profile" : !(profile.skill_ratings || []).length ? "skills" : !profile.assessed ? "assessment" : "ready";

  return (
    <div className="relative min-h-full overflow-y-auto">
      <AmbientBackground density={18} />
      <div className="relative z-10 mx-auto max-w-2xl px-6 py-10">
      <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}>
        <div className="text-center">
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 via-brand-400 to-accent-cyan text-2xl shadow-[0_0_50px_-8px_rgba(124,108,246,0.9)]"
          >
            ⬢
          </motion.div>
          <p className="eyebrow">AI Internship Simulator · Module 1</p>
          <h1 className="mt-2 font-display text-3xl font-bold leading-tight sm:text-4xl">
            Step into your <span className="gradient-text">AI-powered internship</span>
          </h1>
          <p className="mx-auto mt-2 max-w-md text-sm text-text-muted">
            Create an account, record your skills, and complete the assessment. The result becomes the competency profile and the recommended internship.
          </p>
        </div>

        <Stepper step={step} />

        {step === "auth" && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1, duration: 0.35 }}
            className="card mx-auto mt-6 max-w-md space-y-5 p-6"
          >
            <div className="grid grid-cols-2 gap-1 rounded-xl border border-white/[0.06] bg-ink/60 p-1">
              {(["login", "register"]).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={`relative rounded-lg px-3 py-2 text-sm font-medium transition-all duration-200 ${
                    mode === m ? "text-white" : "text-text-muted hover:text-text"
                  }`}
                >
                  {mode === m && (
                    <motion.span
                      layoutId="auth-mode-pill"
                      className="absolute inset-0 rounded-lg bg-gradient-to-r from-brand-500 to-brand-400 shadow-[0_8px_20px_-8px_rgba(124,108,246,0.9)]"
                      transition={{ type: "spring", stiffness: 500, damping: 35 }}
                    />
                  )}
                  <span className="relative">{m === "login" ? "Sign in" : "Create account"}</span>
                </button>
              ))}
            </div>
            <form onSubmit={handleAuth} className="space-y-3">
              {mode === "register" && (
                <Field label="Full name">
                  <input className="input" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
                </Field>
              )}
              <Field label="Email">
                <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
              </Field>
              <Field label="Password">
                <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={mode === "register" ? 8 : 1} required />
              </Field>
              {authError && <p className="rounded-lg border border-status-blocked/30 bg-status-blocked/10 px-3 py-2 text-xs text-status-blocked">{authError}</p>}
              <button type="submit" className="btn-primary w-full py-2.5" disabled={busy}>{busy ? "Please wait..." : mode === "register" ? "Create account" : "Sign in"}</button>
            </form>
            <details className="rounded-xl border border-white/[0.06] bg-ink/40 px-3 py-2 text-xs text-text-faint">
              <summary className="cursor-pointer py-1 transition-colors hover:text-text-muted">Development sign-in</summary>
              <form onSubmit={handleDev} className="mt-2 space-y-3 pb-1">
                <Field label="Student ID">
                  <input className="input" value={studentId} onChange={(e) => setStudentId(e.target.value)} required />
                </Field>
                <Field label="Mode">
                  <select className="input" value={loginRole} onChange={(e) => setLoginRole(e.target.value)}>
                    <option value="student">Student</option>
                    <option value="admin">Admin</option>
                  </select>
                </Field>
                <button type="submit" className="btn-ghost" disabled={busy}>Mint dev token</button>
              </form>
            </details>
          </motion.div>
        )}

        {step === "profile" && (
          <motion.form
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            onSubmit={saveProfile}
            className="card mt-6 space-y-4 p-6"
          >
            <div>
              <p className="eyebrow">Your profile</p>
              <p className="mt-1 text-sm text-text-muted">Tell the workplace who you are and where you want to go.</p>
            </div>
            <Field label="Full name"><input className="input" value={fullName} onChange={(e) => setFullName(e.target.value)} required /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="College"><input className="input" value={college} onChange={(e) => setCollege(e.target.value)} /></Field>
              <Field label="Degree"><input className="input" value={degree} onChange={(e) => setDegree(e.target.value)} /></Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Year of study"><input className="input" type="number" min="1" max="8" value={year} onChange={(e) => setYear(e.target.value)} /></Field>
              <Field label="Target role">
                <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
                  {ROLES.map((item) => <option key={item}>{item}</option>)}
                </select>
              </Field>
            </div>
            <Field label="Industry">
              <select className="input" value={domain} onChange={(e) => setDomain(e.target.value)}>
                {COMPANY_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </Field>
            <Field label="Career goal"><input className="input" value={careerGoal} onChange={(e) => setCareerGoal(e.target.value)} required /></Field>
            {formError && <p className="rounded-lg border border-status-blocked/30 bg-status-blocked/10 px-3 py-2 text-xs text-status-blocked">{formError}</p>}
            <div className="flex gap-2">
              <button type="submit" className="btn-primary flex-1 py-2.5" disabled={busy}>Save profile</button>
              <button type="button" className="btn-ghost" onClick={handleSignOut}>Sign out</button>
            </div>
          </motion.form>
        )}

        {step === "skills" && (
          <motion.form
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            onSubmit={saveSkills}
            className="card mt-6 space-y-4 p-6"
          >
            <div>
              <p className="eyebrow">Skill inventory</p>
              <p className="mt-1 text-sm text-text-muted">Rate the skills you can already demonstrate. 1 is unfamiliar and 5 is confident.</p>
            </div>
            <div className="grid gap-2">
              {catalog.map((skill, i) => {
                const selected = selectedSkills[skill.name] != null;
                return (
                  <motion.label
                    key={skill.id}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: Math.min(i * 0.03, 0.4) }}
                    className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl border px-3.5 py-2.5 transition-all duration-200 ${
                      selected
                        ? "border-brand-400/50 bg-brand-500/10 shadow-[0_0_20px_-8px_rgba(124,108,246,0.7)]"
                        : "border-white/[0.06] bg-white/[0.02] hover:border-brand-400/30 hover:bg-brand-500/[0.05]"
                    }`}
                  >
                    <span className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={(e) => {
                          setSelectedSkills((current) => {
                            const next = { ...current };
                            if (e.target.checked) next[skill.name] = 3;
                            else delete next[skill.name];
                            return next;
                          });
                        }}
                      />
                      {skill.name}
                      <span className="text-[11px] text-text-faint">{skill.category}</span>
                    </span>
                    {selected && (
                      <input
                        className="input w-16"
                        type="number"
                        min="1"
                        max="5"
                        value={selectedSkills[skill.name]}
                        onChange={(e) => setSelectedSkills((current) => ({ ...current, [skill.name]: Number(e.target.value) }))}
                      />
                    )}
                    </motion.label>
                );
              })}
            </div>
            {formError && <p className="rounded-lg border border-status-blocked/30 bg-status-blocked/10 px-3 py-2 text-xs text-status-blocked">{formError}</p>}
            <button type="submit" className="btn-primary w-full py-2.5" disabled={busy}>Save skills</button>
          </motion.form>
        )}

        {step === "assessment" && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6 space-y-4"
          >
            {!attempt ? (
              <div className="card overflow-hidden p-8 text-center">
                <div className="pointer-events-none absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-brand-500/15 to-transparent" />
                <div className="relative">
                  <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500/30 to-accent-cyan/20 text-2xl shadow-[0_0_40px_-10px_rgba(124,108,246,0.8)]">
                    🧠
                  </div>
                  <p className="eyebrow">Baseline assessment</p>
                  <p className="mx-auto mt-2 max-w-md text-sm text-text-muted">The question bank scores technical judgment and workplace decisions. Your answers set the baseline competency profile.</p>
                  {formError && <p className="mx-auto mt-3 max-w-md rounded-lg border border-status-blocked/30 bg-status-blocked/10 px-3 py-2 text-xs text-status-blocked">{formError}</p>}
                  <button type="button" className="btn-primary mt-5 px-8 py-3 text-base" onClick={beginAssessment} disabled={busy}>Start assessment →</button>
                </div>
              </div>
            ) : (
              <form onSubmit={submitAssessment} className="space-y-4">
                {attempt.questions.map((question, index) => (
                  <motion.div
                    key={question.id}
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: Math.min(index * 0.06, 0.5) }}
                    className="card space-y-3 p-5"
                  >
                    <div className="flex items-center gap-2">
                      <span className="chip chip-glow font-mono">Q{index + 1}</span>
                      <p className="text-[11px] uppercase tracking-wider text-text-faint">{question.competency.replaceAll("_", " ")}</p>
                    </div>
                    <p className="text-[15px] font-medium leading-relaxed text-text">{question.prompt}</p>
                    {question.question_type === "mcq" ? (
                      <div className="space-y-2">
                        {(question.options || []).map((option) => {
                          const checked = answers[question.id] === option.id;
                          return (
                            <label
                              key={option.id}
                              className={`flex cursor-pointer items-start gap-3 rounded-xl border px-3.5 py-2.5 text-sm transition-all duration-200 ${
                                checked
                                  ? "border-brand-400/60 bg-brand-500/15 text-text shadow-[0_0_20px_-8px_rgba(124,108,246,0.8)]"
                                  : "border-white/[0.06] bg-white/[0.02] text-text-muted hover:border-brand-400/30 hover:text-text"
                              }`}
                            >
                              <input type="radio" name={question.id} value={option.id} checked={checked} onChange={() => setAnswers((current) => ({ ...current, [question.id]: option.id }))} className="mt-1 accent-[#8b7cf6]" />
                              <span><span className="mr-1.5 font-mono text-text-faint">{option.id}.</span>{option.text}</span>
                            </label>
                          );
                        })}
                      </div>
                    ) : (
                      <textarea className="input min-h-20" value={answers[question.id] || ""} onChange={(e) => setAnswers((current) => ({ ...current, [question.id]: e.target.value }))} />
                    )}
                  </motion.div>
                ))}
                {formError && <p className="rounded-lg border border-status-blocked/30 bg-status-blocked/10 px-3 py-2 text-xs text-status-blocked">{formError}</p>}
                <button type="submit" className="btn-primary w-full py-3 text-base" disabled={busy}>Submit assessment</button>
              </form>
            )}
          </motion.div>
        )}

        {step === "ready" && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6 space-y-4"
          >
            <div className="card overflow-hidden p-6">
              <div className="pointer-events-none absolute inset-x-0 top-0 h-20 bg-gradient-to-b from-status-completed/10 to-transparent" />
              <div className="relative">
                <div className="flex items-center gap-2">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-status-completed/15 text-xs text-status-completed shadow-[0_0_12px_rgba(62,214,149,0.6)]">✓</span>
                  <p className="eyebrow">Baseline complete</p>
                </div>
                <p className="mt-3 text-[15px] leading-relaxed text-text">{profile.assessment_summary}</p>
                <div className="mt-4 grid gap-2 sm:grid-cols-2">
                  <div className="rounded-xl border border-status-completed/20 bg-status-completed/[0.06] px-3.5 py-2.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-status-completed">Strengths</p>
                    <p className="mt-0.5 text-xs text-text-muted">{(profile.strengths || []).join(", ") || "—"}</p>
                  </div>
                  <div className="rounded-xl border border-status-progress/20 bg-status-progress/[0.06] px-3.5 py-2.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-status-progress">Focus areas</p>
                    <p className="mt-0.5 text-xs text-text-muted">{(profile.weaknesses || []).join(", ") || "—"}</p>
                  </div>
                </div>
              </div>
            </div>
            <div className="card space-y-2.5 p-6 text-sm">
              <p className="eyebrow">Recommended internship</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {[
                  ["Role", profile.recommended_parameters?.role],
                  ["Stack", (profile.recommended_parameters?.technology_stack || []).join(", ")],
                  ["Difficulty", profile.recommended_parameters?.difficulty],
                  ["Company", profile.recommended_parameters?.company_type],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-xl border border-white/[0.06] bg-ink/40 px-3.5 py-2.5">
                    <p className="text-[11px] uppercase tracking-wider text-text-faint">{k}</p>
                    <p className="mt-0.5 font-medium text-text">{v || "—"}</p>
                  </div>
                ))}
              </div>
              <p className="pt-1 text-xs text-text-muted">Reinforcement focus: {(profile.recommended_parameters?.focus_competencies || []).join(", ") || "—"}</p>
            </div>
            {simsEnabled && simsLoading && (
              <div className="flex items-center gap-2 text-xs text-text-faint">
                <span className="h-3 w-3 rounded-full border-2 border-brand-400/30 border-t-brand-300 animate-spin" style={{ animationDuration: "0.8s" }} />
                Checking for existing internship progress…
              </div>
            )}
            {resumable && (
              <motion.div
                initial={{ opacity: 0, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                className="card space-y-1 border-accent-cyan/25 bg-gradient-to-br from-accent-cyan/[0.08] to-brand-500/[0.08] p-5 text-sm shadow-[0_0_40px_-12px_rgba(70,216,255,0.4)]"
              >
                <p className="eyebrow">✦ Saved progress found</p>
                <p className="font-display text-base font-semibold">{resumable.title}</p>
                <div className="mt-2">
                  <div className="progress-track h-2">
                    <div className="progress-fill" style={{ width: `${resumable.progress ?? 0}%` }} />
                  </div>
                </div>
                <p className="pt-1 text-text-muted">
                  {resumable.role} · {resumable.status?.replace?.("_", " ")} · {resumable.progress ?? 0}% complete
                </p>
              </motion.div>
            )}
            {notice && (
              <p className="rounded-lg border border-brand-400/30 bg-brand-500/10 px-3 py-2 text-xs text-brand-200" role="status">
                {notice}
              </p>
            )}
            {formError && <p className="rounded-lg border border-status-blocked/30 bg-status-blocked/10 px-3 py-2 text-xs text-status-blocked">{formError}</p>}
            <div className="flex flex-wrap gap-2">
              {resumable ? (
                <>
                  <button
                    type="button"
                    className="btn-primary flex-1 py-3 text-base"
                    onClick={resumeInternship}
                    disabled={generateMutation.isPending}
                  >
                    ▶ Resume Internship Simulation
                  </button>
                  <button type="button" className="btn-ghost" onClick={startInternship} disabled={generateMutation.isPending}>
                    Start a new internship
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="btn-primary flex-1 py-3.5 text-base"
                  onClick={startInternship}
                  disabled={generateMutation.isPending || simsLoading}
                >
                  {generateMutation.isPending ? "Provisioning your workplace…" : "🚀 Start Internship Simulation"}
                </button>
              )}
              <button type="button" className="btn-ghost" onClick={handleSignOut}>Sign out</button>
            </div>
          </motion.div>
        )}
      </motion.div>
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.12em] text-text-faint">{label}</span>
      {children}
    </label>
  );
}
