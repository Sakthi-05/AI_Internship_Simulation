import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { useAuth } from "../context/AuthContext";
import { useSimulation } from "../context/SimulationContext";
import { useGenerateSimulation } from "../hooks/useApi";
import LoadingScreen from "../components/LoadingScreen";
import { apiClient } from "../api/client";

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
  const { setSimulation, clearSimulation } = useSimulation();
  const navigate = useNavigate();
  const generateMutation = useGenerateSimulation();

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
      clearSimulation();
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
  }, [auth, clearSimulation]);

  const handleSignOut = () => {
    logout();
    clearSimulation();
    setProfile(null);
    setAttempt(null);
    setAnswers({});
    setSelectedSkills({});
    setFormError(null);
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
    setBusy(true);
    try {
      await apiClient.post(`/api/assessments/${attempt.id}/submit`, { answers: payload });
      const res = await apiClient.get("/api/students/me");
      setProfile(res.data);
      setAttempt(null);
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const startInternship = async (event) => {
    event.preventDefault();
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
      const result = await generateMutation.mutateAsync({
        student_id: auth.studentId,
        role: recommendation.role,
        technology_stack: recommendation.technology_stack,
        difficulty: recommendation.difficulty,
        company_type: recommendation.company_type,
      });
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
      });
      navigate("/simulation");
    } catch (err) {
      setFormError(err.message);
    }
  };

  if (generateMutation.isPending || (auth?.token && auth.role !== "admin" && !profile)) {
    return <LoadingScreen />;
  }

  const step = !auth?.token || !auth?.studentId ? "auth" : !profile ? "profile" : !profile.profile_complete ? "profile" : !(profile.skill_ratings || []).length ? "skills" : !profile.assessed ? "assessment" : "ready";

  return (
    <div className="mx-auto h-full max-w-2xl overflow-y-auto px-6 py-10">
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
        <p className="text-[10px] uppercase tracking-[0.18em] text-status-todo">Module 1</p>
        <h1 className="font-display text-2xl font-semibold text-text">Student profile and baseline assessment</h1>
        <p className="mt-1 text-sm text-text-muted">
          Create an account, record your skills, and complete the assessment. The result becomes the competency profile and the recommended internship.
        </p>

        {step === "auth" && (
          <div className="mt-8 space-y-6">
            <div className="flex gap-2">
              <button type="button" className={mode === "login" ? "btn-primary" : "btn-ghost"} onClick={() => setMode("login")}>Sign in</button>
              <button type="button" className={mode === "register" ? "btn-primary" : "btn-ghost"} onClick={() => setMode("register")}>Create account</button>
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
              {authError && <p className="text-xs text-status-blocked">{authError}</p>}
              <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Please wait..." : mode === "register" ? "Create account" : "Sign in"}</button>
            </form>
            <details className="text-xs text-text-faint">
              <summary className="cursor-pointer">Development sign-in</summary>
              <form onSubmit={handleDev} className="mt-3 space-y-3">
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
          </div>
        )}

        {step === "profile" && (
          <form onSubmit={saveProfile} className="mt-8 space-y-3">
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
            {formError && <p className="text-xs text-status-blocked">{formError}</p>}
            <div className="flex gap-2">
              <button type="submit" className="btn-primary" disabled={busy}>Save profile</button>
              <button type="button" className="btn-ghost" onClick={handleSignOut}>Sign out</button>
            </div>
          </form>
        )}

        {step === "skills" && (
          <form onSubmit={saveSkills} className="mt-8 space-y-3">
            <p className="text-sm text-text-muted">Rate the skills you can already demonstrate. 1 is unfamiliar and 5 is confident.</p>
            <div className="grid gap-2">
              {catalog.map((skill) => {
                const selected = selectedSkills[skill.name] != null;
                return (
                  <label key={skill.id} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
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
                  </label>
                );
              })}
            </div>
            {formError && <p className="text-xs text-status-blocked">{formError}</p>}
            <button type="submit" className="btn-primary" disabled={busy}>Save skills</button>
          </form>
        )}

        {step === "assessment" && (
          <div className="mt-8 space-y-4">
            {!attempt ? (
              <>
                <p className="text-sm text-text-muted">The question bank scores technical judgment and workplace decisions. Your answers set the baseline competency profile.</p>
                {formError && <p className="text-xs text-status-blocked">{formError}</p>}
                <button type="button" className="btn-primary" onClick={beginAssessment} disabled={busy}>Start assessment</button>
              </>
            ) : (
              <form onSubmit={submitAssessment} className="space-y-4">
                {attempt.questions.map((question, index) => (
                  <div key={question.id} className="card space-y-2">
                    <p className="text-xs uppercase tracking-wide text-text-faint">{question.competency.replaceAll("_", " ")}</p>
                    <p className="text-sm text-text">{index + 1}. {question.prompt}</p>
                    {question.question_type === "mcq" ? (
                      <div className="space-y-1">
                        {(question.options || []).map((option) => (
                          <label key={option.id} className="flex items-start gap-2 text-sm text-text-muted">
                            <input type="radio" name={question.id} value={option.id} checked={answers[question.id] === option.id} onChange={() => setAnswers((current) => ({ ...current, [question.id]: option.id }))} />
                            <span>{option.id}. {option.text}</span>
                          </label>
                        ))}
                      </div>
                    ) : (
                      <textarea className="input min-h-20" value={answers[question.id] || ""} onChange={(e) => setAnswers((current) => ({ ...current, [question.id]: e.target.value }))} />
                    )}
                  </div>
                ))}
                {formError && <p className="text-xs text-status-blocked">{formError}</p>}
                <button type="submit" className="btn-primary" disabled={busy}>Submit assessment</button>
              </form>
            )}
          </div>
        )}

        {step === "ready" && (
          <form onSubmit={startInternship} className="mt-8 space-y-4">
            <div className="card">
              <p className="text-xs uppercase tracking-wide text-text-faint">Baseline summary</p>
              <p className="mt-1 text-sm text-text">{profile.assessment_summary}</p>
              <p className="mt-3 text-xs text-text-muted">Strengths: {(profile.strengths || []).join(", ") || "—"}</p>
              <p className="text-xs text-text-muted">Focus areas: {(profile.weaknesses || []).join(", ") || "—"}</p>
            </div>
            <div className="card space-y-1 text-sm">
              <p className="text-xs uppercase tracking-wide text-text-faint">Recommended internship</p>
              <p>Role: {profile.recommended_parameters?.role}</p>
              <p>Stack: {(profile.recommended_parameters?.technology_stack || []).join(", ")}</p>
              <p>Difficulty: {profile.recommended_parameters?.difficulty}</p>
              <p>Company: {profile.recommended_parameters?.company_type}</p>
              <p>Reinforcement focus: {(profile.recommended_parameters?.focus_competencies || []).join(", ")}</p>
            </div>
            {formError && <p className="text-xs text-status-blocked">{formError}</p>}
            <div className="flex gap-2">
              <button type="submit" className="btn-primary">Start recommended internship</button>
              <button type="button" className="btn-ghost" onClick={handleSignOut}>Sign out</button>
            </div>
          </form>
        )}
      </motion.div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-text-faint">{label}</span>
      {children}
    </label>
  );
}
