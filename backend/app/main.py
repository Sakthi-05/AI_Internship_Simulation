"""
AI Workflow Simulation Engine — backend entrypoint.

Phase 1: DB schema, generators, Kanban/task REST APIs.
Phase 2: (frontend, separate app).
Phase 3: AI Project Manager + AI Teammates chat, WebSockets, Dynamic
Events Engine, Stateful Workflow rules, Meetings, Notifications,
Activity Log, Timeline, and a real APScheduler background job.
"""
import logging
from time import perf_counter
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    activity, assessments, auth, chat, companies, dev_auth, events, generate, meetings,
    notifications, projects, reviews, simulation, sprints, students, submissions, tasks, team, timeline, ws, intelligence, admin,
)
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.agents.specialists.technical_review_agent import create_technical_review_orchestrator
from app.agents.specialists.competency_agent import register_competency_agent
from app.agents.specialists.adaptation_agent import register_adaptation_agent
from app.agents.specialists.career_insights_agent import register_career_insights_agent
from app.agents.specialists.manager_agent import register_manager_agent
from app.services.scheduler_jobs import check_high_stress_projects, send_proactive_workplace_messages

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(check_high_stress_projects, "interval", seconds=60, id="high_stress_checkin")
    scheduler.add_job(send_proactive_workplace_messages, "interval", seconds=90, id="proactive_workplace_chat")
    scheduler.start()
    logger.info("Application startup complete")
    yield
    scheduler.shutdown(wait=False)
    logger.info("Application shutdown complete")


app = FastAPI(
    title="AI Workflow Simulation Engine",
    description=(
        "Simulates a real software company internship: generates a company, "
        "project, sprints, and tasks, then drives task/Kanban state, AI "
        "manager/teammate chat, dynamic events, meetings, and notifications "
        "through a persistent PostgreSQL-backed workflow."
    ),
    version="0.3.0-phase3",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

_PERFORMANCE_SENSITIVE_ROUTES = {
    "/api/simulations/{simulation_id}",
    "/api/simulations/{simulation_id}/tasks/current",
    "/api/simulations/{simulation_id}/progress",
    "/api/simulations/{simulation_id}/tasks/{task_id}/events",
    "/api/companies/{company_id}/manager",
    "/api/projects/{project_id}/state",
}


@app.middleware("http")
async def log_slow_module2_requests(request, call_next):
    """Log slow requested Module 2 reads without logging student data or IDs."""
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (perf_counter() - started) * 1000
        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        if route_path in _PERFORMANCE_SENSITIVE_ROUTES and elapsed_ms >= 1000:
            logger.warning(
                "Slow Module 2 request method=%s route=%s status=500 duration_ms=%.1f",
                request.method, route_path, elapsed_ms,
            )
        raise

    elapsed_ms = (perf_counter() - started) * 1000
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if route_path in _PERFORMANCE_SENSITIVE_ROUTES and elapsed_ms >= 1000:
        logger.warning(
            "Slow Module 2 request method=%s route=%s status=%s duration_ms=%.1f",
            request.method, route_path, response.status_code, elapsed_ms,
        )
    return response

# The registry and agent instance are application-owned. Request-scoped DB and
# provider dependencies are bound only for the duration of each review call.
app.state.agent_orchestrator = create_technical_review_orchestrator()
register_competency_agent(app.state.agent_orchestrator.registry)
register_manager_agent(app.state.agent_orchestrator.registry)
register_career_insights_agent(app.state.agent_orchestrator.registry)
register_adaptation_agent(app.state.agent_orchestrator.registry,
                          orchestrator=app.state.agent_orchestrator)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register exception handlers
register_exception_handlers(app)

app.include_router(auth.router)
app.include_router(students.router)
app.include_router(assessments.router)
app.include_router(dev_auth.router)
app.include_router(generate.router)
app.include_router(companies.router)
app.include_router(projects.router)
app.include_router(sprints.router)
app.include_router(tasks.router)
app.include_router(simulation.router)
app.include_router(chat.router)
app.include_router(team.router)
app.include_router(notifications.router)
app.include_router(events.router)
app.include_router(activity.router)
app.include_router(meetings.router)
app.include_router(timeline.router)
app.include_router(submissions.router)
app.include_router(reviews.router)
app.include_router(ws.router)
app.include_router(intelligence.router)
app.include_router(admin.router)


@app.get("/health", tags=["System"])
def health_check():
    return {"status": "ok", "environment": settings.ENVIRONMENT}
