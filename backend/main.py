import os
import secrets
from contextlib import asynccontextmanager
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.database import engine, Base
from backend.config import JOBS_DIR, LOCAL_API_TOKEN, ensure_data_root


INTERRUPTED_JOB_MESSAGE = "上一次分析在应用或后端退出时中断，请重新分析。若反复出现，请发送本机日志。"


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_data_root()

    # Startup: create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Reset any stuck jobs (analyzing/preprocessing -> failed)
    from backend.models import Job, _now
    from sqlalchemy import update
    async with engine.begin() as conn:
        await conn.execute(
            update(Job)
            .where(Job.status.in_(["preprocessing", "preprocessing_done", "analyzing", "cancelling"]))
            .values(status="failed", error_message=INTERRUPTED_JOB_MESSAGE, updated_at=_now())
        )

    yield

    # Shutdown: dispose engine
    await engine.dispose()


app = FastAPI(title="Framebench", lifespan=lifespan)


def get_allowed_origins() -> list[str]:
    frontend_port = os.getenv("FRAMEBENCH_FRONTEND_PORT") or os.getenv("FILM_MASTER_FRONTEND_PORT") or "5174"
    return [
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        f"http://127.0.0.1:{frontend_port}",
        f"http://localhost:{frontend_port}",
        "null",  # Electron file:// protocol; API token is still required.
    ]


@app.middleware("http")
async def require_local_token(request: Request, call_next):
    if request.method == "OPTIONS" or not LOCAL_API_TOKEN:
        return await call_next(request)

    if not request.url.path.startswith("/api") or request.url.path == "/api/health":
        return await call_next(request)

    provided = (
        request.headers.get("x-framebench-token")
        or request.headers.get("x-film-master-token")
        or request.query_params.get("token")
        or ""
    )
    if not secrets.compare_digest(provided, LOCAL_API_TOKEN):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve extracted frames as static files
os.makedirs(JOBS_DIR, exist_ok=True)
app.mount("/api/frames", StaticFiles(directory=JOBS_DIR), name="frames")

from backend.routers import jobs, sse
app.include_router(jobs.router)
app.include_router(sse.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
