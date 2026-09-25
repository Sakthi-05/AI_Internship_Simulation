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
      <div>
        <p className="text-[10px] uppercase tracking-[0.18em] text-status-todo">Administration</p>
        <h1 className="font-display text-2xl font-semibold">Students and question bank</h1>
        <p className="text-sm text-text-muted">Review student readiness and maintain the baseline assessment.</p>
      </div>
      {error && <div className="rounded border border-status-blocked/30 bg-status-blocked/10 p-3 text-xs text-status-blocked">{error}</div>}
      <div className="card overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-border text-xs uppercase tracking-wide text-text-faint">
              <th className="p-3">Student</th><th className="p-3">Domain</th><th className="p-3">Role</th><th className="p-3">Avg</th><th className="p-3">Simulations</th><th className="p-3">Readiness</th>
            </tr>
          </thead>
          <tbody>
            {students.map((s) => (
              <tr key={s.student_id} className="border-b border-border-soft">
                <td className="p-3 font-mono">{s.full_name || s.student_id}</td>
                <td className="p-3">{s.domain || "—"}</td>
                <td className="p-3">{s.role || "—"}</td>
                <td className="p-3">{s.average_score == null ? "—" : `${Math.round(s.average_score)}%`}</td>
                <td className="p-3">{s.simulations}</td>
                <td className="p-3">{s.readiness ? `${Math.round(s.readiness.readiness_score)}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {students.length === 0 && <p className="p-6 text-center text-sm text-text-faint">No student profiles have been created yet.</p>}
      </div>
      <div className="card space-y-3">
        <h2 className="text-sm font-medium">Question bank</h2>
        <form onSubmit={addQuestion} className="flex flex-wrap gap-2">
          <input className="input min-w-64 flex-1" value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="New short-answer prompt" required minLength={8} />
          <input className="input w-52" value={competency} onChange={(e) => setCompetency(e.target.value)} />
          <button className="btn-primary" type="submit">Add</button>
        </form>
        <ul className="space-y-2 text-sm">
          {questions.filter((q) => q.active).map((q) => (
            <li key={q.id} className="flex items-start justify-between gap-3 border-b border-border-soft py-2">
              <span><span className="text-text-faint">{q.competency}</span> — {q.prompt}</span>
              <button type="button" className="btn-ghost" onClick={() => deactivate(q.id)}>Retire</button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
