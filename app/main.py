import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.routers import cancel, dashboard, monitoring, reports, service
from app.seed import seed_database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        count = seed_database(db)
        logger.info("Database ready with %d services.", count)
    finally:
        db.close()
    yield


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
    ],
)


@app.middleware("http")
async def rate_limit_headers(request: Request, call_next) -> Response:
    response: Response = await call_next(request)
    if hasattr(request.state, "rate_limit_daily"):
        daily_remaining = max(
            0, request.state.rate_limit_daily - request.state.rate_limit_daily_used - 1
        )
        response.headers["X-RateLimit-Limit"] = str(request.state.rate_limit_daily)
        response.headers["X-RateLimit-Remaining"] = str(daily_remaining)
    return response


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
    return {"status": "ok", "version": settings.api_version}


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
