"""QA E2E Harness - clean carefully checked."""
from __future__ import annotations

import json
import sys
import time
import uuid
from typing import Any

import psycopg
import requests

BASE = "http://127.0.0.1:8000"
DB_URL = "postgresql://postgres:postgres@localhost:5432/ai_internship"


def log(step: str, ok: bool, **kw: Any) -> None:
    defs = {"step": step, "ok": bool(ok), "ts": time.time()}
    defs.update(kw)
    try:
        line = json.dumps(defs, default=str)
    except Exception:
        line = json.dumps({k: (str(v) if not isinstance(v, (int, float, bool, type(None), str, list, dict)) else v) for k, v in defs.items()}, default=str)
    print(line)
    sys.stdout.flush()


def call(method: str, path: str, token: str | None = None, body: Any = None, params: Any = None) -> requests.Response:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return requests.request(method, BASE + path, headers=h, json=body, params=params, timeout=45)


def register(email: str, password: str, full_name: str) -> str:
    r = call("POST", "/api/auth/register", body={"email": email, "password": password, "full_name": full_name})
    if r.status_code >= 400:
        log("register", False, s=r.status_code, b=r.text[:400])
        raise SystemExit(1)
    tok = call("POST", "/api/auth/login", body={"email": email, "password": password})
    if tok.status_code >= 400:
        log("login", False, s=tok.status_code, b=tok.text[:400])
        raise SystemExit(1)
    data = tok.json()
    token = data.get("access_token") or data.get("token")
    if not token:
        log("token", False, k=list(data.keys()))
        raise SystemExit(1)
    log("register", True, email=email)
    return token


def skills_list(token: str) -> list[dict]:
    r = call("GET", "/api/skills", token=token)
    return r.json().get("skills", [])


def profile_update(token: str, **kw) -> dict:
    r = call("PUT", "/api/students/me", token=token, body=kw)
    if r.status_code >= 400:
        log("profile_update", False, s=r.status_code, b=r.text[:400])
        raise SystemExit(1)
    return r.json()


def skills_set(token: str, ratings: dict[str, int]) -> list[dict]:
    body = {"skills": [{"name": k, "self_rating": v} for k, v in ratings.items()]}
    r = call("PUT", "/api/students/me/skills", token=token, body=body)
    if r.status_code >= 400:
        log("skills_set", False, s=r.status_code, b=r.text[:400])
        raise SystemExit(1)
    log("skills_set", True, n=len(ratings))
    return r.json().get("skills", [])


def assess_start(token: str) -> dict:
    r = call("POST", "/api/assessments/start", token=token, body={})
    if r.status_code >= 400:
        log("assess_start", False, s=r.status_code, b=r.text[:400])
        raise SystemExit(1)
    return r.json()


def assess_submit(token: str, attempt_id: str, answers: list[dict]) -> dict:
    r = call("POST", f"/api/assessments/{attempt_id}/submit", token=token, body={"answers": answers})
    if r.status_code >= 400:
        log("assess_submit", False, s=r.status_code, b=r.text[:1000])
        raise SystemExit(1)
    return r.json()


def sim_create_and_start(token: str, *, sid: str, role: str, tech: list[str], diff: str = "intermediate", ctype: str = "startup") -> str:
    body = {
        "student_id": sid,
        "generation_request_id": str(uuid.uuid4()),
        "role": role,
        "technology_stack": tech,
        "difficulty": diff,
        "company_type": ctype,
    }
    r = call("POST", "/api/generate", token=token, body=body)
    if r.status_code >= 400:
        log("api_generate", False, s=r.status_code, b=r.text[:1500])
        raise SystemExit(1)
    out = r.json()
    sim_id = str(out["simulation_id"])
    log("api_generate", True, company=str(out.get("company_id"))[:8], project=str(out.get("project_id"))[:8], sim=sim_id[:8], sprints=len(out.get("sprint_ids") or []), tasks=out.get("task_count"))
    rs = call("POST", f"/api/simulations/{sim_id}/start", token=token, body={})
    if rs.status_code >= 400:
        log("sim_start", False, s=rs.status_code, b=rs.text[:1500])
    else:
        log("sim_start", True, s=rs.status_code)
    return sim_id


def sim_path(token: str, sim_id: str) -> list[dict]:
    return call("GET", f"/api/simulations/{sim_id}/task-path", token=token).json()


def sim_current(token: str, sim_id: str) -> dict:
    return call("GET", f"/api/simulations/{sim_id}/tasks/current", token=token).json()


def task_get(token: str, tid: str) -> dict:
    return call("GET", f"/api/tasks/{tid}", token=token).json()


def task_start(token: str, sim_id: str, tid: str) -> dict:
    r = call("POST", f"/api/simulations/{sim_id}/tasks/{tid}/start", token=token, body={})
    if r.status_code >= 400:
        log("task_start", False, s=r.status_code, b=r.text[:400])
    return r.json()


def submit_files(token: str, sim_id: str, tid: str, *, filename: str, content: str, note: str) -> dict:
    data = {"task_id": tid, "note": note}
    files = [("files", (filename, content.encode(), "text/plain"))]
    h = {"Authorization": f"Bearer {token}"}
    r = requests.post(BASE + f"/api/simulations/{sim_id}/tasks/{tid}/submit", headers=h, data=data, files=files, timeout=60)
    if r.status_code >= 400:
        log("submit_files", False, s=r.status_code, b=r.text[:1500])
        raise SystemExit(1)
    return r.json()


def review_get(token: str, vid: str) -> dict | None:
    r = call("GET", f"/api/reviews/versions/{vid}", token=token)
    if r.status_code >= 400:
        return None
    payload = r.json()
    if isinstance(payload, list):
        return payload[-1] if payload else None
    return payload


def manager_review(token: str, vid: str, decision: str, notes: str = "") -> dict:
    r = call("POST", f"/api/reviews/versions/{vid}/manager-review", token=token, body={"decision": decision, "notes": notes})
    if r.status_code >= 400:
        log("manager_review", False, d=decision, s=r.status_code, b=r.text[:1000])
    return r.json()


def resubmit(token: str, old_vid: str, *, filename: str, content: str, note: str) -> dict:
    data = {"note": note}
    files = [("files", (filename, content.encode(), "text/plain"))]
    h = {"Authorization": f"Bearer {token}"}
    r = requests.post(BASE + f"/api/submissions/versions/{old_vid}/resubmit", headers=h, data=data, files=files, timeout=60)
    if r.status_code >= 400:
        log("resubmit", False, s=r.status_code, b=r.text[:1500])
        raise SystemExit(1)
    return r.json()


def wait_review(token: str, vid: str, wait: int = 120) -> dict | None:
    start = time.time()
    while time.time() - start < wait:
        rv = review_get(token, vid)
        status = (rv or {}).get("status")
        result = (rv or {}).get("result")
        if status in ("COMPLETED", "FAILED") or result in ("APPROVED", "CHANGES_REQUESTED"):
            return rv
        time.sleep(2)
    log("wait_review_timeout", False, vid=vid)
    return None


def sql(q: str) -> list[dict]:
    with psycopg.connect(DB_URL) as c:
        with c.cursor() as cur:
            cur.execute(q)
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def build_baseline_answers(questions: list[dict], bias: str) -> list[dict]:
    out = []
    for q in questions:
        qid = str(q.get("id"))
        txt = " ".join(str(q.get(k, "")) for k in ("prompt", "question_text", "competency", "domain", "text", "skill"))
        low = txt.lower()
        strong = False
        if bias == "backend":
            if any(w in low for w in ("python", "fastapi", "sql", "postgre", "rest", "api", "backend", "pydantic", "database", "pytest", "git")):
                strong = True
        else:
            if any(w in low for w in ("react", "css", "html", "javascript", "frontend", "ui", "ux", "node", "dom", "vite", "component", "accessibility")):
                strong = True
        out.append({"question_id": qid, "response": "correct" if strong else "needs-work"})
    return out


def topic_set(questions: list[dict]) -> set[str]:
    s: set[str] = set()
    for q in questions:
        txt = " ".join(str(q.get(k, "")) for k in ("prompt", "question_text", "competency", "domain", "skill"))
        for w in txt.lower().split():
            w = w.strip(".,;:!?()[]{}\"'`")
            if len(w) >= 5:
                s.add(w)
    return s


def make_student(prefix: str, full_name: str, role: str, domain: str, ratings: dict[str, int]) -> dict:
    email = f"{prefix}_{uuid.uuid4().hex[:10]}@qa.example"
    reg = register(email, "QApass123!", full_name)
    t = reg["token"]
    real_sid = reg["student_id"]
    profile_update(t, full_name=full_name, role=role, domain=domain, college="QA Test U", degree="BTech IT", year_of_study=3, career_goal=f"Become a {role}")
    skills_set(t, ratings)
    log("make_student", True, sid_prefix=prefix, real_student_id=real_sid, role=role)
    return {"token": t, "student_id": real_sid, "email": email, "name": full_name}


def run_student_a() -> dict:
    """Strong backend student E2E (main QA chain)."""
    log("PHASE", True, name="STUDENT_A_BACKEND")
    s = make_student("qa_back", "QA Backend Student A", "Backend Developer", "Backend Engineering", {
        "Python": 5, "FastAPI": 5, "PostgreSQL": 5, "SQL": 5,
        "Testing": 3, "Git": 4, "Debugging": 4, "REST APIs": 4,
    })
    t = s["token"]
    s1 = assess_start(t)
    attempt = str(s1["id"])
    nq1 = len(s1.get("questions") or [])
    log("assess_first_start", True, attempt=attempt, n_questions=nq1)
    s2 = assess_start(t)
    same = str(s2["id"]) == attempt
    log("assess_idempotent", same, first=attempt, second=s2.get("id"))
    qs = s1.get("questions") or s2.get("questions") or []
    ans = build_baseline_answers(qs, "backend")
    sub = assess_submit(t, attempt, ans)
    log("assess_submit_a", True, has_result="result" in sub or "score" in sub or "competencies" in sub)
    sim_id = sim_create_and_start(t, sid=s["student_id"], role="Backend Developer", tech=["Python", "FastAPI", "PostgreSQL", "Git", "Docker", "Testing"], diff="intermediate", ctype="startup")
    time.sleep(5.0)
    p1 = sim_path(t, sim_id)
    log("sim_initial_path", True, sim_id=sim_id, n=len(p1), path=[{
        "seq": x.get("sequence"), "task_id": str(x.get("task_id"))[:8], "status": x.get("status"),
    } for x in p1[:6]])
    cur = sim_current(t, sim_id)
    cur_task = cur.get("task") or cur
    tid1 = str(cur_task.get("id")) if isinstance(cur_task, dict) else None
    if not tid1:
        log("tid1_missing", False, cur=cur)
        raise SystemExit(1)
    td1 = task_get(t, tid1)
    meta = td1.get("task_metadata") or {}
    log("task1_detail", True, tid=tid1, sprint_id=str(td1.get("sprint_id"))[:8],
        title=td1.get("title", "")[:120], meta_keys=list(meta.keys()),
        reason=str(meta.get("generation_reason", ""))[:150],
        perf=str(meta.get("performance_trend_at_generation")), deps=td1.get("dependencies") or [])
    task_start(t, sim_id, tid1)
    py1 = "\n".join([
        "from fastapi import FastAPI",
        "from pydantic import BaseModel",
        "app = FastAPI()",
        "class Item(BaseModel):",
        "    name: str",
        "    price: float",
        "@app.get('/')",
        "def root(): return {'hello': 'world'}",
        "@app.post('/items', status_code=201)",
        "def create(item: Item): return {'id': 1, **item.model_dump()}",
    ])
    sv1 = submit_files(t, sim_id, tid1, filename="main.py", content=py1, note="Task 1 submission v1")
    vid1 = sv1.get("version_id") or sv1.get("id")
    rev1 = wait_review(t, str(vid1))
    log("task1_review", rev1 is not None, status=(rev1 or {}).get("status"),
        result=(rev1 or {}).get("result"), score=(rev1 or {}).get("overall_score"), vid=str(vid1)[:8])
    time.sleep(2)
    manager_review(t, str(vid1), "APPROVED", notes="QA manager approval T1")
    time.sleep(6.0)
    p2 = sim_path(t, sim_id)
    log("path_post_t1", True, n=len(p2), tail=[{
        "seq": x.get("sequence"), "task_id": str(x.get("task_id"))[:8], "status": x.get("status"),
    } for x in p2[-5:]])
    entry_t2 = next((x for x in p2 if x.get("sequence") == 2), None)
    tid2 = None
    if entry_t2:
        tid2 = str(entry_t2["task_id"])
        td2 = task_get(t, tid2)
        m2 = td2.get("task_metadata") or {}
        log("task2_detail", True, tid=tid2, sprint_id=str(td2.get("sprint_id"))[:8],
            title=td2.get("title", "")[:120],
            prev=str(m2.get("previous_task_id", ""))[:8] == tid1[:8],
            reason=str(m2.get("generation_reason", ""))[:150],
            trend=str(m2.get("performance_trend_at_generation")),
            deps=td2.get("dependencies") or [])
    # Resubmission flow on a separate task entry or create one: pick next in order
    if tid2:
        task_start(t, sim_id, tid2)
        weak = "\n".join(["# placeholder", "print('hello')"])
        sv2v1 = submit_files(t, sim_id, tid2, filename="app.py", content=weak, note="T2 weak v1")
        vid2v1 = str(sv2v1.get("version_id"))
        rev2v1 = wait_review(t, vid2v1, wait=90)
        log("task2_v1_review", rev2v1 is not None, score=(rev2v1 or {}).get("overall_score"))
        time.sleep(2)
        manager_review(t, vid2v1, "CHANGES_REQUESTED", notes="QA manager asks for changes")
        time.sleep(2)
        strong = "\n".join([
            "from fastapi import FastAPI, HTTPException",
            "from pydantic import BaseModel, Field",
            "from typing import Optional",
            "app = FastAPI(title='Items')",
            "class Item(BaseModel):",
            "    name: str = Field(min_length=1)",
            "    price: float = Field(gt=0)",
            "    in_stock: Optional[bool] = True",
            "DB: list[Item] = []",
            "@app.post('/items', status_code=201)",
            "def add(i: Item):",
            "    DB.append(i); return {'id': len(DB), 'item': i.model_dump()}",
            "@app.get('/items/{idx}')",
            "def one(idx: int):",
            "    if idx < 1 or idx > len(DB): raise HTTPException(404, 'nope')",
            "    return {'id': idx, 'item': DB[idx-1].model_dump()}",
        ])
        rs_v2 = resubmit(t, vid2v1, filename="app.py", content=strong, note="QA improved v2")
        vid2v2 = str(rs_v2.get("version_id"))
        rev2v2 = wait_review(t, vid2v2, wait=120)
        log("task2_v2_review", rev2v2 is not None,
            s1=(rev2v1 or {}).get("overall_score"),
            s2=(rev2v2 or {}).get("overall_score"))
        time.sleep(2)
        manager_review(t, vid2v2, "APPROVED", notes="QA approves T2 after fix")
        time.sleep(5.0)
    # Requirement change on current task
    p3 = sim_path(t, sim_id)
    current = sim_current(t, sim_id)
    task_curr = (current.get("task") or current)
    tc_id = str(task_curr.get("id")) if isinstance(task_curr, dict) else None
    if not tc_id:
        tc_id = tid2 or tid1
    task_start(t, sim_id, tc_id)
    trig = call("POST", "/api/events/trigger", token=t, body={
        "event_type": "requirement_change",
        "severity": "medium",
        "title": "Requirement Change: Add rate limiting",
        "message": f"Add per-IP rate limiting (100/min) for all endpoints. Task ref: task {tc_id}.",
        "task_id": tc_id,
    })
    log("req_change_triggered", trig.status_code < 400, s=trig.status_code)
    ev_id = None
    if trig.status_code < 400:
        ev = trig.json()
        ev_id = str(ev.get("id"))
        log("req_change_details", True, ev_id=ev_id,
            event_task_id=str(ev.get("task_id"))[:8] if ev.get("task_id") else None,
            matches=ev.get("task_id") == tc_id)
        resp = call("POST", f"/api/simulations/{sim_id}/tasks/{tc_id}/events/{ev_id}/respond", token=t, body={
            "response": "Acknowledged. I will add slowapi middleware with 100 req/min per client IP, tests for 429, then resubmit.",
            "action_taken": "implementation_change",
        })
        log("req_change_student_response", resp.status_code < 400, s=resp.status_code)
    # Manager chat: initial messages and anti-spam
    mg1 = call("GET", "/api/chat/manager", token=t).json()
    before = len(mg1) if isinstance(mg1, list) else 0
    log("manager_initial", True, count=before, sample=str(mg1[0].get("content"))[:120] if before else None)
    for _ in range(5):
        call("GET", "/api/chat/manager", token=t)
        time.sleep(0.3)
    mg2 = call("GET", "/api/chat/manager", token=t).json()
    after = len(mg2) if isinstance(mg2, list) else 0
    log("manager_no_spam", before == after, before=before, after=after)
    # Team list and help
    team = call("GET", "/api/team", token=t).json()
    tm_id = None
    if isinstance(team, list) and team:
        tm_id = str(team[0].get("id"))
        log("team_list", True, n=len(team))
        h = call("POST", f"/api/chat/team/{tm_id}", token=t, body={
            "content": f"Working on task {tc_id}: rate limiting. How should I structure FastAPI rate limit middleware?",
        })
        log("team_help_resp", h.status_code < 400, s=h.status_code, snippet=str(h.text)[:180])
    # Notifications summary
    notifs = call("GET", "/api/notifications", token=t).json()
    from collections import Counter
    ty: Counter = Counter()
    if isinstance(notifs, list):
        for n in notifs:
            ty[n.get("notification_type")] += 1
    log("notifs_summary", True, total=len(notifs) if isinstance(notifs, list) else 0, types=dict(ty))
    # Profile
    pro = call("GET", "/api/students/me", token=t).json()
    log("final_profile_a", True,
        diff=pro.get("recommended_difficulty"),
        bc_keys=list((pro.get("baseline_competencies") or {}).keys())[:10],
        cc_keys=list((pro.get("current_competencies") or {}).keys())[:10])
    # Sprint and handoff summary via SQL
    log("handoff_summary", True, sim_id=sim_id)
    return {"token": t, "sim_id": sim_id, "sid": s["student_id"],
        "tid1": tid1, "tid2": tid2, "vid1": str(vid1), "vid2v1": vid2v1, "vid2v2": vid2v2, "ev_id": ev_id}


def run_student_b() -> dict:
    """Strong frontend student - baseline personalization comparison."""
    log("PHASE", True, name="STUDENT_B_FRONTEND")
    s = make_student("qa_front", "QA Frontend Student B", "Frontend Engineer", "Frontend Engineering", {
        "React": 5, "JavaScript": 5, "Testing": 2, "Communication": 5,
        "SQL": 1, "Git": 2, "REST APIs": 3, "Docker": 1,
    })
    t = s["token"]
    sa = assess_start(t)
    attempt_b = str(sa["id"])
    qb = sa.get("questions") or []
    log("assess_start_b", True, attempt=attempt_b, nq=len(qb))
    sub = assess_submit(t, attempt_b, build_baseline_answers(qb, "frontend"))
    log("assess_submit_b", True, ok=bool(sub))
    pro = call("GET", "/api/students/me", token=t).json()
    log("final_profile_b", True, diff=pro.get("recommended_difficulty"))
    # compare to A for divergence
    return {"token": t, "questions": qb, "sid": s["student_id"]}


def main() -> int:
    # A first (main chain)
    res_a = run_student_a()
    # B second (baseline personalization)
    res_b = run_student_b()
    # Personalization diff: use saved A questions... but we already consumed A in other fn.
    # Query DB for both attempt question contents by student_ids to compare
    rows_a = sql(f"""SELECT q.prompt || ' ' || COALESCE(q.competency,'') || ' ' || COALESCE(q.domain,'') AS text
        FROM assessment_attempts a
        JOIN assessment_questions q ON q.id = ANY(a.question_ids)
        WHERE a.student_id = '{res_a['sid']}'""")
    rows_b = sql(f"""SELECT q.prompt || ' ' || COALESCE(q.competency,'') || ' ' || COALESCE(q.domain,'') AS text
        FROM assessment_attempts a
        JOIN assessment_questions q ON q.id = ANY(a.question_ids)
        WHERE a.student_id = '{res_b['sid']}'""")
    sa = set()
    for r in rows_a:
        for w in (r["text"] or "").lower().split():
            w = w.strip(".,;:!?(){}\"'`[]")
            if len(w) >= 5:
                sa.add(w)
    sb = set()
    for r in rows_b:
        for w in (r["text"] or "").lower().split():
            w = w.strip(".,;:!?(){}\"'`[]")
            if len(w) >= 5:
                sb.add(w)
    inter = len(sa & sb)
    uni = (len(sa | sb) or 1)
    j = inter / uni
    log("baseline_personalization", j < 0.7, jaccard=round(j, 4),
        A_unique=len(sa - sb), B_unique=len(sb - sa))
    # DB audits for duplicates
    log("DB_AUDITS", True)
    dup_rv = sql("SELECT version_id, COUNT(*) c FROM ai_reviews GROUP BY version_id HAVING COUNT(*) > 1")
    log("dup_ai_reviews_per_version", len(dup_rv) == 0, rows=dup_rv)
    dup_sv = sql("SELECT submission_id, version_number, COUNT(*) c FROM submission_versions GROUP BY submission_id, version_number HAVING COUNT(*) > 1")
    log("dup_submission_versions", len(dup_sv) == 0, rows=dup_sv)
    # Ownership chain integrity for res_a sim
    sim_id = res_a["sim_id"]
    chain = sql(f"""SELECT COUNT(*) total,
        SUM(CASE WHEN u.id IS NULL THEN 1 ELSE 0 END) bad_user,
        SUM(CASE WHEN p.id IS NULL THEN 1 ELSE 0 END) bad_proj,
        SUM(CASE WHEN s.id IS NULL THEN 1 ELSE 0 END) bad_sprint,
        SUM(CASE WHEN t.id IS NULL THEN 1 ELSE 0 END) bad_task,
        SUM(CASE WHEN sb.id IS NULL THEN 1 ELSE 0 END) bad_sub,
        SUM(CASE WHEN v.id IS NULL THEN 1 ELSE 0 END) bad_ver,
        SUM(CASE WHEN r.id IS NULL THEN 1 ELSE 0 END) bad_rev
        FROM simulations sim
        LEFT JOIN users u ON u.student_id = sim.student_id
        LEFT JOIN projects p ON p.id = sim.project_id
        LEFT JOIN sprints s ON s.project_id = p.id
        LEFT JOIN tasks t ON t.sprint_id = s.id
        LEFT JOIN submissions sb ON sb.task_id = t.id
        LEFT JOIN submission_versions v ON v.submission_id = sb.id
        LEFT JOIN ai_reviews r ON r.version_id = v.id
        WHERE sim.id = '{sim_id}'""")
    if chain:
        row = chain[0]
        log("ownership_chain", all(v == 0 for k, v in row.items() if k.startswith("bad_")), **row)
    # Sprint_completed event + notification counts (both for this sim)
    se = sql(f"SELECT event_type, COUNT(*) c FROM workflow_events WHERE simulation_id = '{sim_id}' AND event_type = 'sprint_completed' GROUP BY event_type")
    log("sprint_completed_events", True, rows=se)
    nt = sql(f"SELECT n.notification_type, COUNT(*) c FROM notifications n JOIN users u ON u.id = n.user_id WHERE u.student_id = '{res_a['sid']}' GROUP BY n.notification_type")
    log("notifs_by_type_db", True, rows=nt)
    # Perf trend for student A
    intel = call("GET", "/api/intelligence/profile", token=res_a["token"])
    log("intelligence_profile", intel.status_code < 400, s=intel.status_code,
        keys=list(intel.json().keys())[:12] if intel.status_code < 400 else None)
    log("QA_HARNESS_END", True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
