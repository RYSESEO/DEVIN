import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.routers import cancel, dashboard, monitoring, reports, service
from app.seed import seed_database

# Structured logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("cancelkit")

WEB_DIR = Path(__file__).parent.parent / "web"

# Whether to start the background scheduler (disable in tests)
ENABLE_SCHEDULER = os.getenv("CANCELKIT_SCHEDULER", "true").lower() in ("true", "1", "yes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run Alembic migrations if available; fall back to create_all for dev/testing
    try:
        from alembic.config import Config as AlembicConfig

        from alembic import command as alembic_cmd

        alembic_cfg = AlembicConfig("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
        alembic_cmd.upgrade(alembic_cfg, "head")
        logger.info("Alembic migrations applied.")
    except Exception:
        logger.info("Alembic unavailable — using create_all fallback.")
        Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        count = seed_database(db)
        logger.info("Database ready with %d services.", count)
    finally:
        db.close()

    # Start background scheduler
    scheduler = None
    if ENABLE_SCHEDULER:
        try:
            from app.scheduler import start_scheduler

            scheduler = start_scheduler()
            logger.info("Background scheduler started.")
        except Exception:
            logger.exception("Failed to start scheduler — running without it.")

    yield

    # Stop scheduler on shutdown
    if scheduler:
        try:
            from app.scheduler import stop_scheduler

            stop_scheduler()
        except Exception:
            logger.exception("Error stopping scheduler.")


app = FastAPI(
    title=settings.api_title,
    description=settings.api_description,
    version=settings.api_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "Retry-After",
        "X-Path-Confidence",
        "X-CancelKit-Event",
        "X-CancelKit-Signature",
        "X-Request-ID",
    ],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next) -> Response:
    """Add request ID, timing, and rate-limit headers to every response."""
    request_id = str(uuid.uuid4())[:8]
    start = time.time()

    try:
        response: Response = await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error. Please try again."},
        )

    duration_ms = round((time.time() - start) * 1000, 1)
    response.headers["X-Request-ID"] = request_id

    # Rate-limit headers from auth middleware
    if hasattr(request.state, "rate_limit_daily"):
        daily_remaining = max(
            0, request.state.rate_limit_daily - request.state.rate_limit_daily_used - 1
        )
        response.headers["X-RateLimit-Limit"] = str(request.state.rate_limit_daily)
        response.headers["X-RateLimit-Remaining"] = str(daily_remaining)

    # Log API requests (skip static files and health checks)
    if not request.url.path.startswith("/static") and request.url.path != "/health":
        logger.info(
            "%s %s → %d (%sms) [%s]",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )

    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again."},
    )


app.include_router(service.router)
app.include_router(cancel.router)
app.include_router(reports.router)
app.include_router(dashboard.router)
app.include_router(monitoring.router)


@app.get("/", include_in_schema=False)
async def landing_page():
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse(
        "<h1>CancelKit API</h1>"
        "<p>Visit <a href='/docs'>/docs</a> for API docs.</p>"
    )


@app.get("/dashboard", include_in_schema=False)
async def dashboard_page():
    dash_path = WEB_DIR / "dashboard.html"
    if dash_path.exists():
        return FileResponse(dash_path, media_type="text/html")
    return HTMLResponse("<h1>Dashboard coming soon</h1>")


@app.get("/health")
async def health_check():
    result = {"status": "ok", "version": settings.api_version}

    if ENABLE_SCHEDULER:
        try:
            from app.scheduler import get_scheduler_status

            result["scheduler"] = get_scheduler_status()
        except Exception:
            result["scheduler"] = {"running": False, "error": "import_failed"}

    return result


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
