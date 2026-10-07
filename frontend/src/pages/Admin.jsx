import { useEffect, useState } from "react";
import { apiClient } from "../api/client";

export default function Admin() {
  const [students, setStudents] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [error, setError] = useState("");
  const [prompt, setPrompt] = useState("");
  const [competency, setCompetency] = useState("technical_knowledge");

  const load = () => {
    apiClient.get("/api/intelligence/admin/students").then((r) => setStudents(r.data.students || [])).catch((e) => setError(e.message));
    apiClient.get("/api/admin/questions").then((r) => setQuestions(r.data.questions || [])).catch((e) => setError(e.message));
  };

  useEffect(() => { load(); }, []);

  const addQuestion = async (event) => {
    event.preventDefault();
    setError("");
    try {
      await apiClient.post("/api/admin/questions", {
        prompt,
        competency,
        question_type: "short_answer",
        rubric_keywords: ["because", "risk", "test"],
      });
      setPrompt("");
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const deactivate = async (id) => {
    setError("");
    try {
      await apiClient.delete(`/api/admin/questions/${id}`);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-6">
      <div className="card overflow-hidden p-6">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-20 bg-gradient-to-b from-brand-500/15 to-transparent" />
        <div className="relative">
          <p className="eyebrow">🛡 Administration</p>
          <h1 className="mt-1 font-display text-2xl font-bold sm:text-3xl">Mission <span className="gradient-text">Control</span></h1>
          <p className="mt-1 text-sm text-text-muted">Review student readiness and maintain the baseline assessment.</p>
        </div>
      </div>
      {error && <div className="rounded-xl border border-status-blocked/30 bg-status-blocked/10 p-3 text-xs text-status-blocked">{error}</div>}
      <div className="card overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/[0.06] bg-white/[0.02] px-4 py-3">
          <h2 className="font-display text-sm font-semibold">Student fleet</h2>
          <span className="chip font-mono text-[11px]!">{students.length} profiles</span>
        </div>
        <div className="overflow-x-auto">
        <table className="table-premium w-full text-left text-sm">
          <thead>
            <tr className="border-b border-white/[0.06] bg-ink/40 text-[11px] uppercase tracking-wider text-text-faint">
              <th className="p-3">Student</th><th className="p-3">Domain</th><th className="p-3">Role</th><th className="p-3">Avg</th><th className="p-3">Simulations</th><th className="p-3">Readiness</th>
            </tr>
          </thead>
          <tbody>
            {students.map((s) => (
              <tr key={s.student_id} className="border-b border-white/[0.04] last:border-0">
                <td className="p-3">
                  <span className="flex items-center gap-2">
                    <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500/60 to-accent-cyan/40 font-display text-xs font-bold text-white">
                      {(s.full_name || s.student_id)?.[0]?.toUpperCase()}
                    </span>
                    <span className="font-mono text-xs">{s.full_name || s.student_id}</span>
                  </span>
                </td>
                <td className="p-3 text-text-muted">{s.domain || "—"}</td>
                <td className="p-3 text-text-muted">{s.role || "—"}</td>
                <td className="p-3 font-mono">{s.average_score == null ? "—" : `${Math.round(s.average_score)}%`}</td>
                <td className="p-3 font-mono">{s.simulations}</td>
                <td className="p-3">
                  {s.readiness ? (
                    <span className="chip font-mono text-[11px]! text-status-completed! border-status-completed/30! bg-status-completed/10!">
                      {Math.round(s.readiness.readiness_score)}%
                    </span>
                  ) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        {students.length === 0 && <p className="p-6 text-center text-sm text-text-faint">No student profiles have been created yet.</p>}
      </div>
      <div className="card space-y-3 p-5">
        <h2 className="font-display text-sm font-semibold">📚 Question bank</h2>
        <form onSubmit={addQuestion} className="flex flex-wrap gap-2">
          <input className="input min-w-64 flex-1" value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="New short-answer prompt" required minLength={8} />
          <input className="input w-52" value={competency} onChange={(e) => setCompetency(e.target.value)} />
          <button className="btn-primary" type="submit">+ Add</button>
        </form>
        <ul className="space-y-2 text-sm">
          {questions.filter((q) => q.active).map((q) => (
            <li key={q.id} className="flex items-start justify-between gap-3 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5 transition-colors hover:border-brand-400/25">
              <span><span className="chip mr-2 font-mono text-[10px]!">{q.competency}</span><span className="text-text-muted">{q.prompt}</span></span>
              <button type="button" className="btn-ghost shrink-0" onClick={() => deactivate(q.id)}>Retire</button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
