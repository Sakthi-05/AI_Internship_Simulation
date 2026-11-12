import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import "./OnboardingAuth.css";
import { useAuth } from "../context/AuthContext";
import { useSimulation } from "../context/SimulationContext";
import { useGenerateSimulation } from "../hooks/useApi";
import { pickResumableSimulation, toSimulationContext, useSimulationList } from "../hooks/useSimulationApi";
import LoadingScreen from "../components/LoadingScreen";
import AmbientBackground from "../components/AmbientBackground";
import AssessmentHistory from "../components/AssessmentHistory";
import { apiClient } from "../api/client";
import {
  buildTechnicalProfilePayload,
  getDomainTechnologies,
  loadTechnicalProfile,
  ratingsFromTechnicalProfile,
  saveTechnicalProfile as saveTechnicalProfileRequest,
  technicalProfileState,
} from "../lib/technicalProfile";

const STEP_ORDER = ["auth", "profile", "technical-profile", "assessment", "ready"];
const STEP_LABELS = {
  auth: "Sign in",
  profile: "Profile",
  "technical-profile": "Technical profile",
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
    <div className="onboarding-stepper mt-6 flex items-center gap-1.5">
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
  const [email, setEmail] = useState(() => {
    try { return localStorage.getItem("aiwse:remembered-email") || ""; }
    catch { return ""; }
  });
  const [rememberMe, setRememberMe] = useState(() => {
    try { return Boolean(localStorage.getItem("aiwse:remembered-email")); }
    catch { return false; }
  });
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [fullName, setFullName] = useState("");
  const [studentId, setStudentId] = useState("student_001");
  const [loginRole, setLoginRole] = useState("student");
  const [authError, setAuthError] = useState(null);
  const [authNotice, setAuthNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  const [profile, setProfile] = useState(null);
  const [technicalDomains, setTechnicalDomains] = useState([]);
  const [technicalProfile, setTechnicalProfile] = useState(null);
  const [technicalProfileLoaded, setTechnicalProfileLoaded] = useState(false);
  const [technicalProfileLoading, setTechnicalProfileLoading] = useState(false);
  const [technicalProfileError, setTechnicalProfileError] = useState(null);
  const [technicalEditing, setTechnicalEditing] = useState(false);
  const [technicalDomainId, setTechnicalDomainId] = useState("");
  const [technicalRatings, setTechnicalRatings] = useState({});
  const [attempt, setAttempt] = useState(null);
  const [assessmentEvaluation, setAssessmentEvaluation] = useState(null);
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
      setTechnicalProfile(null);
      setTechnicalDomains([]);
      setTechnicalProfileLoaded(false);
      setTechnicalProfileLoading(false);
      setTechnicalProfileError(null);
      setTechnicalEditing(false);
      setTechnicalDomainId("");
      setTechnicalRatings({});
      setAttempt(null);
      setAssessmentEvaluation(null);
      setAnswers({});
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
    setTechnicalProfile(null);
    setTechnicalDomains([]);
    setTechnicalProfileLoaded(false);
    setTechnicalProfileLoading(true);
    setTechnicalProfileError(null);
    setTechnicalEditing(false);
    setTechnicalDomainId("");
    setTechnicalRatings({});
    loadTechnicalProfile(apiClient).then(({ domains, profile: savedProfile }) => {
      if (cancelled) return;
      setTechnicalDomains(domains);
      setTechnicalProfile(savedProfile);
      setTechnicalDomainId(savedProfile?.status === "active" ? savedProfile.technical_domain_id : "");
      setTechnicalRatings(ratingsFromTechnicalProfile(savedProfile));
      setTechnicalProfileLoaded(true);
    }).catch((err) => {
      if (!cancelled) setTechnicalProfileError(err.message || "Could not load your technical profile.");
    }).finally(() => {
      if (!cancelled) setTechnicalProfileLoading(false);
    });
    apiClient.get("/api/students/me").then((res) => {
      if (cancelled) return;
      const data = res.data;
      setProfile(data);
      if (data.assessed) {
        apiClient.get("/api/assessments/latest").then((result) => {
          if (!cancelled) setAssessmentEvaluation(result.data?.result?.evaluation || null);
        }).catch(() => {});
      } else {
        setAssessmentEvaluation(null);
      }
      setFullName(data.full_name || auth.fullName || "");
      setCollege(data.college || "");
      setDegree(data.degree || "");
      setYear(String(data.year_of_study || 3));
      setCareerGoal(data.career_goal || "");
      setRole(data.role || "Backend Developer");
      setDomain(data.domain || "startup");
    }).catch((err) => setFormError(err.message));
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
    setTechnicalProfile(null);
    setTechnicalDomains([]);
    setTechnicalProfileLoaded(false);
    setTechnicalProfileLoading(false);
    setTechnicalProfileError(null);
    setTechnicalEditing(false);
    setTechnicalDomainId("");
    setTechnicalRatings({});
    setAttempt(null);
    setAssessmentEvaluation(null);
    setAnswers({});
    setFormError(null);
    setNotice(null);
    setAuthError(null);
    setMode("login");
    navigate("/", { replace: true });
  };

  const handleAuth = async (event) => {
    event.preventDefault();
    setAuthError(null);
    setAuthNotice(null);
    setBusy(true);
    try {
      const session = mode === "register"
        ? await register({ email, password, fullName })
        : await login(email, password);
      if (mode === "login") {
        try {
          if (rememberMe) localStorage.setItem("aiwse:remembered-email", email.trim());
          else localStorage.removeItem("aiwse:remembered-email");
        } catch {
          // Remembering the email is optional; authentication has already succeeded.
        }
      }
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

  const refreshTechnicalProfile = async () => {
    setTechnicalProfileLoading(true);
    setTechnicalProfileError(null);
    try {
      const { domains, profile: savedProfile } = await loadTechnicalProfile(apiClient);
      setTechnicalDomains(domains);
      setTechnicalProfile(savedProfile);
      setTechnicalDomainId(savedProfile?.status === "active" ? savedProfile.technical_domain_id : "");
      setTechnicalRatings(ratingsFromTechnicalProfile(savedProfile));
      setTechnicalProfileLoaded(true);
    } catch (err) {
      setTechnicalProfileError(err.message || "Could not load your technical profile.");
    } finally {
      setTechnicalProfileLoading(false);
    }
  };

  const handleTechnicalDomainChange = (nextDomainId) => {
    setTechnicalDomainId(nextDomainId);
    const isSavedDomain = technicalProfile?.status === "active"
      && nextDomainId === technicalProfile.technical_domain_id;
    setTechnicalRatings(isSavedDomain ? ratingsFromTechnicalProfile(technicalProfile) : {});
    setTechnicalProfileError(null);
  };

  const handleSaveTechnicalProfile = async (event) => {
    event.preventDefault();
    setTechnicalProfileError(null);
    const technologies = getDomainTechnologies(technicalDomains, technicalDomainId);
    let payload;
    try {
      payload = buildTechnicalProfilePayload(technicalDomainId, technologies, technicalRatings);
    } catch (err) {
      setTechnicalProfileError(err.message);
      return;
    }
    setBusy(true);
    try {
      const savedProfile = await saveTechnicalProfileRequest(apiClient, payload);
      setTechnicalProfile(savedProfile);
      setTechnicalDomainId(savedProfile.technical_domain_id);
      setTechnicalRatings(ratingsFromTechnicalProfile(savedProfile));
      setTechnicalProfileLoaded(true);
      setTechnicalEditing(false);
    } catch (err) {
      setTechnicalProfileError(err.message || "Could not save your technical profile.");
    } finally {
      setBusy(false);
    }
  };

  const editTechnicalProfile = () => {
    setTechnicalDomainId(technicalProfile.technical_domain_id);
    setTechnicalRatings(ratingsFromTechnicalProfile(technicalProfile));
    setTechnicalProfileError(null);
    setTechnicalEditing(true);
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
      setAssessmentEvaluation(submitRes.data?.evaluation || null);
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
            const latest = await apiClient.get("/api/assessments/latest");
            setAssessmentEvaluation(latest.data?.result?.evaluation || null);
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
        generationSource: result.generation_source,
        status: "not_started",
        studentId: auth.studentId,
      });
      navigate("/simulation");
    } catch (err) {
      const detail = /timeout|network/i.test(err?.message || "")
        ? "The generation request timed out. It may still be processing; retry safely to recover the same simulation."
        : err.message;
      setFormError(detail);
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

  const step = !auth?.token || !auth?.studentId ? "auth"
    : !profile ? "profile"
      : !profile.profile_complete ? "profile"
            : !technicalProfileLoaded || technicalProfileState(technicalProfile) !== "active" || technicalEditing ? "technical-profile"
          : !profile.assessed ? "assessment" : "ready";
  const currentTechnicalTechnologies = getDomainTechnologies(technicalDomains, technicalDomainId);
  const technicalRatingsComplete = currentTechnicalTechnologies.length > 0
    && currentTechnicalTechnologies.every((technology) => {
      const rating = technicalRatings[technology.id];
      return Number.isInteger(rating) && rating >= 1 && rating <= 5;
    });

  if (step === "auth") {
    return (
      <main className="auth-page">
        <div className="auth-layout">
          <section className="auth-story" aria-labelledby="auth-story-title">
            <div className="auth-story-inner">
              <a className="auth-brand" href="/" aria-label="AI Internship Simulator home">
                <span className="auth-brand-mark" aria-hidden="true">
                  <svg viewBox="0 0 32 32" fill="none">
                    <path d="M16 3.8 26.6 9.9v12.2L16 28.2 5.4 22.1V9.9L16 3.8Z" stroke="currentColor" strokeWidth="1.7" />
                    <path d="m11.1 18.7 3.2-7.2 2.1 4.4 1.4-2.7 3.1 5.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
                <span className="auth-brand-copy">
                  <strong>AI Internship Simulator</strong>
                  <small>Adaptive Workflow Simulation</small>
                </span>
              </a>

              <div className="auth-story-copy">
                <span className="auth-kicker"><i /> A workplace built for your next step</span>
                <h1 id="auth-story-title">Make your next move <span>feel familiar.</span></h1>
                <p>Practice the work, decisions, and collaboration of a real team—then bring that confidence into your career.</p>
              </div>

              <div className="auth-visual" aria-label="Connected simulation workflow illustration">
                <svg className="auth-visual-lines" viewBox="0 0 620 350" fill="none" aria-hidden="true">
                  <path d="M71 245C142 245 133 111 235 111s87 132 186 132 76-125 154-125" stroke="url(#auth-line-a)" strokeWidth="1.4" />
                  <path d="M76 292c97 0 91-97 174-97 93 0 86 90 174 90 67 0 78-72 149-72" stroke="url(#auth-line-b)" strokeWidth="1" strokeDasharray="4 7" />
                  <circle cx="71" cy="245" r="5" fill="#A5B4FC" />
                  <circle cx="235" cy="111" r="5" fill="#C4B5FD" />
                  <circle cx="421" cy="243" r="5" fill="#93C5FD" />
                  <circle cx="575" cy="118" r="5" fill="#A5B4FC" />
                  <defs>
                    <linearGradient id="auth-line-a" x1="71" y1="111" x2="575" y2="243" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#818CF8" stopOpacity=".14" />
                      <stop offset=".52" stopColor="#A78BFA" stopOpacity=".8" />
                      <stop offset="1" stopColor="#60A5FA" stopOpacity=".18" />
                    </linearGradient>
                    <linearGradient id="auth-line-b" x1="76" y1="195" x2="573" y2="292" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#60A5FA" stopOpacity=".12" />
                      <stop offset=".52" stopColor="#818CF8" stopOpacity=".48" />
                      <stop offset="1" stopColor="#A78BFA" stopOpacity=".14" />
                    </linearGradient>
                  </defs>
                </svg>
                <div className="auth-orbit auth-orbit-one" />
                <div className="auth-orbit auth-orbit-two" />
                <div className="auth-work-card auth-work-card-main">
                  <div className="auth-work-card-top"><span className="auth-work-mark">AI</span><span>Simulation workspace</span><b>LIVE</b></div>
                  <div className="auth-work-title">Project workflow</div>
                  <div className="auth-work-flow">
                    <div className="auth-flow-step auth-flow-done"><span>✓</span><small>Understand</small></div>
                    <i />
                    <div className="auth-flow-step auth-flow-active"><span>02</span><small>Build</small></div>
                    <i />
                    <div className="auth-flow-step"><span>03</span><small>Review</small></div>
                  </div>
                  <div className="auth-work-progress"><span /><span /><span /></div>
                  <div className="auth-work-foot"><span>Adaptive task path</span><span>01 / 04</span></div>
                </div>
                <div className="auth-float-chip auth-float-left"><span>✳</span> Real team scenarios</div>
                <div className="auth-float-chip auth-float-right"><span>↗</span> Skills in action</div>
              </div>

              <div className="auth-story-foot">
                <span>Build practical experience, one sprint at a time.</span>
                <span className="auth-story-foot-dot" aria-hidden="true" />
                <span>Designed for early-career talent</span>
              </div>
            </div>
          </section>

          <section className="auth-form-panel" aria-label={mode === "login" ? "Sign in" : "Create account"}>
            <motion.div
              className="auth-form-wrap"
              key={mode}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.28, ease: "easeOut" }}
            >
              <div className="auth-mobile-brand">
                <span className="auth-brand-mark" aria-hidden="true">⬡</span>
                <strong>AI Internship Simulator</strong>
              </div>
              <div className="auth-form-heading">
                <span className="auth-secure-label"><i /> Secure workspace access</span>
                <h2>{mode === "login" ? "Welcome back" : "Create your account"}</h2>
                <p>{mode === "login" ? "Sign in to continue your workplace simulation." : "Set up your account to begin your career simulation."}</p>
              </div>

              <form className="auth-form" onSubmit={handleAuth}>
                {mode === "register" && (
                  <AuthField label="Full name">
                    <input className="auth-input" autoComplete="name" value={fullName} onChange={(event) => setFullName(event.target.value)} required placeholder="Your name" />
                  </AuthField>
                )}
                <AuthField label="Email address">
                  <span className="auth-input-icon" aria-hidden="true">
                    <svg viewBox="0 0 20 20" fill="none"><rect x="2.6" y="4.2" width="14.8" height="11.6" rx="2.3" stroke="currentColor" strokeWidth="1.4" /><path d="m3.5 5.3 6.5 5.2 6.5-5.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  </span>
                  <input className="auth-input auth-input-with-icon" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required placeholder="you@example.com" />
                </AuthField>
                <AuthField label="Password">
                  <span className="auth-input-icon" aria-hidden="true">
                    <svg viewBox="0 0 20 20" fill="none"><rect x="3.5" y="8.2" width="13" height="9" rx="2" stroke="currentColor" strokeWidth="1.4" /><path d="M6.3 8.2V5.9a3.7 3.7 0 0 1 7.4 0v2.3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
                  </span>
                  <input className="auth-input auth-input-with-icon auth-password-input" type={showPassword ? "text" : "password"} autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} minLength={mode === "register" ? 8 : 1} required placeholder={mode === "register" ? "At least 8 characters" : "Enter your password"} />
                  <button className="auth-password-toggle" type="button" onClick={() => setShowPassword((visible) => !visible)} aria-label={showPassword ? "Hide password" : "Show password"} aria-pressed={showPassword}>
                    {showPassword ? "Hide" : "Show"}
                  </button>
                </AuthField>

                {mode === "login" && (
                  <div className="auth-options">
                    <label className="auth-remember">
                      <input type="checkbox" checked={rememberMe} onChange={(event) => setRememberMe(event.target.checked)} />
                      <span>Remember me</span>
                    </label>
                    <button className="auth-text-button" type="button" onClick={() => { setAuthError(null); setAuthNotice("Password reset isn’t available yet. Please contact your platform administrator."); }}>
                      Forgot password?
                    </button>
                  </div>
                )}

                {authError && <p className="auth-message auth-message-error" role="alert">{authError}</p>}
                {authNotice && <p className="auth-message auth-message-note" role="status">{authNotice}</p>}
                <button className="auth-submit" type="submit" disabled={busy}>
                  <span>{busy ? "Please wait…" : mode === "register" ? "Create account" : "Sign in"}</span>
                  {!busy && <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 10h11M10 5l5 5-5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>}
                </button>
              </form>

              <p className="auth-switch-mode">
                {mode === "login" ? "New to the platform?" : "Already have an account?"}{" "}
                <button type="button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setAuthError(null); setAuthNotice(null); }}>
                  {mode === "login" ? "Create an account" : "Sign in"}
                </button>
              </p>

              <details className="auth-dev-signin">
                <summary>Development sign-in</summary>
                <form onSubmit={handleDev}>
                  <AuthField label="Student ID"><input className="auth-input" value={studentId} onChange={(event) => setStudentId(event.target.value)} required /></AuthField>
                  <AuthField label="Mode"><select className="auth-input" value={loginRole} onChange={(event) => setLoginRole(event.target.value)}><option value="student">Student</option><option value="admin">Admin</option></select></AuthField>
                  <button className="auth-dev-button" type="submit" disabled={busy}>Mint development token</button>
                </form>
              </details>
              <p className="auth-legal">By continuing, you agree to use the platform responsibly and keep your account credentials private.</p>
            </motion.div>
          </section>
        </div>
      </main>
    );
  }

  return (
    <main className="auth-page onboarding-auth-page">
      <div className="auth-layout onboarding-auth-layout">
        <section className="auth-story onboarding-story" aria-labelledby="onboarding-story-title">
          <div className="auth-story-inner onboarding-story-inner">
            <a className="auth-brand" href="/" aria-label="AI Internship Simulator home">
              <span className="auth-brand-mark" aria-hidden="true">
                <svg viewBox="0 0 32 32" fill="none">
                  <path d="M16 3.8 26.6 9.9v12.2L16 28.2 5.4 22.1V9.9L16 3.8Z" stroke="currentColor" strokeWidth="1.7" />
                  <path d="m11.1 18.7 3.2-7.2 2.1 4.4 1.4-2.7 3.1 5.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <span className="auth-brand-copy"><strong>AI Internship Simulator</strong><small>Adaptive Workflow Simulation</small></span>
            </a>
            <div className="auth-story-copy onboarding-story-copy">
              <span className="auth-kicker"><i /> Module 1 · Your career profile</span>
              <h1 id="onboarding-story-title">Build a path <span>that fits you.</span></h1>
              <p>Share your goals, show what you know, and get a workplace simulation shaped around your skills.</p>
            </div>
            <div className="onboarding-story-visual" aria-hidden="true">
              <div className="onboarding-orbit onboarding-orbit-a" />
              <div className="onboarding-orbit onboarding-orbit-b" />
              <div className="onboarding-preview-card">
                <div className="onboarding-preview-top"><span className="auth-work-mark">AI</span><span>Personalized career path</span><b>MODULE 01</b></div>
                <div className="onboarding-preview-title">From your skills to real work</div>
                <div className="onboarding-preview-steps"><span>Profile</span><i /><span>Assessment</span><i /><span>Internship</span></div>
                <div className="onboarding-preview-track"><span style={{ width: `${Math.max(20, ((STEP_ORDER.indexOf(step) + 1) / STEP_ORDER.length) * 100)}%` }} /></div>
                <div className="onboarding-preview-foot"><span>Designed around your strengths</span><span>{String(STEP_ORDER.indexOf(step) + 1).padStart(2, "0")} / 05</span></div>
              </div>
            </div>
            <div className="auth-story-foot onboarding-story-foot"><span>Practical experience starts with a clear picture of you.</span></div>
          </div>
        </section>
        <section className="auth-form-panel onboarding-form-panel" aria-label="Internship profile setup">
          <div className="onboarding-flow-page relative min-h-full overflow-y-auto">
      <AmbientBackground density={18} />
      <div className="onboarding-flow-content relative z-10 mx-auto max-w-2xl px-6 py-10">
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

        {technicalProfileState(technicalProfile) === "active" && step !== "technical-profile" && (
          <section className="card mt-4 space-y-3 p-5" aria-label="Saved technical profile">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="eyebrow">Technical familiarity · Self-reported</p>
                <p className="mt-1 text-sm font-semibold text-text">{technicalProfile.domain?.name || technicalProfile.technical_domain_id}</p>
                <p className="mt-1 text-xs text-text-muted">These ratings describe your familiarity and are not verified skill scores.</p>
              </div>
              <button type="button" className="btn-ghost" onClick={editTechnicalProfile}>Edit</button>
            </div>
            <div className="flex flex-wrap gap-2">
              {(technicalProfile.technology_ratings || []).map((technology) => (
                <span className="chip" key={technology.technology_id}>
                  {technology.name}: {technology.self_rating ?? "Not rated"}
                </span>
              ))}
            </div>
          </section>
        )}

        {step === "technical-profile" && (
          <motion.form
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            onSubmit={handleSaveTechnicalProfile}
            className="card mt-6 space-y-4 p-6"
          >
            <div>
              <p className="eyebrow">Technical domain profile</p>
              <p className="mt-1 text-sm text-text-muted">
                Select one domain and report your familiarity with each listed technology. These are self-reported ratings, not verified skill scores.
              </p>
            </div>
            {technicalProfileLoading && (
              <p className="text-sm text-text-muted" role="status">Loading your technical profile and domain catalog…</p>
            )}
            {technicalProfileState(technicalProfile) === "unavailable" && (
              <p className="rounded-lg border border-status-progress/30 bg-status-progress/10 px-3 py-2 text-xs text-text-muted" role="status">
                Your previously selected domain “{technicalProfile.technical_domain_id}” is unavailable. Choose a valid domain to continue; no replacement has been selected for you.
              </p>
            )}
            {technicalProfileError && (
              <div className="rounded-lg border border-status-blocked/30 bg-status-blocked/10 px-3 py-2 text-xs text-status-blocked" role="alert">
                <p>{technicalProfileError}</p>
                {!technicalProfileLoaded && <button type="button" className="mt-2 underline" onClick={refreshTechnicalProfile} disabled={technicalProfileLoading}>Retry loading</button>}
              </div>
            )}
            {technicalProfileLoaded && technicalDomains.length === 0 && !technicalProfileError && (
              <p className="text-sm text-text-muted" role="status">No technical domains are currently available. Try loading again later.</p>
            )}
            {technicalProfileLoaded && technicalDomains.length > 0 && (
              <>
                <Field label="Technical domain">
                  <select
                    className="input"
                    value={technicalDomainId}
                    onChange={(event) => handleTechnicalDomainChange(event.target.value)}
                    required
                  >
                    <option value="">Choose one domain</option>
                    {technicalDomains.map((domainItem) => (
                      <option key={domainItem.id} value={domainItem.id}>{domainItem.name}</option>
                    ))}
                  </select>
                </Field>
                {currentTechnicalTechnologies.length > 0 && (
                  <div className="space-y-2">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-text-faint">Rate every technology · 1 to 5</p>
                    {currentTechnicalTechnologies.map((technology) => (
                      <div key={technology.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium">{technology.name}</p>
                          <p className="text-xs text-text-faint">{technology.description}</p>
                        </div>
                        <label className="flex items-center gap-2 text-xs text-text-muted">
                          Familiarity
                          <select
                            className="input w-24"
                            value={technicalRatings[technology.id] ?? ""}
                            onChange={(event) => setTechnicalRatings((current) => ({
                              ...current,
                              [technology.id]: event.target.value ? Number(event.target.value) : "",
                            }))}
                            aria-label={`${technology.name} self-reported familiarity from 1 to 5`}
                            required
                          >
                            <option value="">Choose</option>
                            {[1, 2, 3, 4, 5].map((rating) => <option key={rating} value={rating}>{rating}</option>)}
                          </select>
                        </label>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
            {technicalProfileLoaded && technicalDomains.length > 0 && (
              <div className="flex gap-2">
                <button type="submit" className="btn-primary flex-1 py-2.5" disabled={busy || !technicalRatingsComplete}>
                  {busy ? "Saving…" : "Save technical profile"}
                </button>
                {technicalEditing && technicalProfileState(technicalProfile) === "active" && (
                  <button type="button" className="btn-ghost" onClick={() => setTechnicalEditing(false)} disabled={busy}>Cancel</button>
                )}
              </div>
            )}
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
                  <p className="mx-auto mt-2 max-w-md text-sm text-text-muted">Questions assess the technologies in your selected domain. Your familiarity ratings guide coverage and difficulty; your answers independently establish your baseline competency.</p>
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
                {typeof assessmentEvaluation?.overall_score === "number" && (
                  <p className="mt-2 font-mono text-sm text-status-completed">
                    Overall score: {assessmentEvaluation.overall_score.toFixed(2)}/100
                  </p>
                )}
                {assessmentEvaluation?.answers?.length > 0 && (
                  <div className="mt-5 space-y-3">
                    <p className="eyebrow">Answer feedback</p>
                    {assessmentEvaluation.answers.map((answer) => (
                      <article key={answer.question_id} className="rounded-xl border border-white/[0.06] bg-ink/40 p-3.5">
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-xs font-semibold uppercase tracking-wider text-text-faint">{answer.competency?.replaceAll("_", " ") || "Technical"}</p>
                          <span className="chip font-mono">{Math.round(answer.score)}/100</span>
                        </div>
                        <p className="mt-2 text-sm text-text">{answer.question}</p>
                        <p className="mt-2 text-xs text-text-muted"><span className="font-semibold text-text">Your answer:</span> {answer.response}</p>
                        <p className="mt-2 text-xs text-text-muted">{answer.explanation}</p>
                        <p className="mt-1 text-xs text-text-muted">{answer.feedback}</p>
                      </article>
                    ))}
                  </div>
                )}
                <div className="mt-4 grid gap-2 sm:grid-cols-2">
                  <div className="rounded-xl border border-status-completed/20 bg-status-completed/[0.06] px-3.5 py-2.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-status-completed">Strengths</p>
                    <p className="mt-0.5 text-xs text-text-muted">{(profile.strengths || []).join(", ") || "—"}</p>
                  </div>
                  <div className="rounded-xl border border-status-progress/20 bg-status-progress/[0.06] px-3.5 py-2.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-status-progress">Focus areas</p>
                    <p className="mt-0.5 text-xs text-text-muted">{(profile.weaknesses || []).join(", ") || "No evidenced focus areas yet"}</p>
                  </div>
                </div>
                {(profile.baseline_unassessed_competencies || []).length > 0 && (
                  <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-text-faint">Not assessed at baseline</p>
                    <p className="mt-0.5 text-xs text-text-muted">{profile.baseline_unassessed_competencies.map((key) => key.replaceAll("_", " ")).join(", ")}</p>
                  </div>
                )}
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
            <AssessmentHistory />
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
        </section>
      </div>
    </main>
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

function AuthField({ label, children }) {
  return (
    <label className="auth-field">
      <span className="auth-field-label">{label}</span>
      <span className="auth-field-control">{children}</span>
    </label>
  );
}
