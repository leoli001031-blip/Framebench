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

        from sqlalchemy import text
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_category ON jobs(category)",
            "CREATE INDEX IF NOT EXISTS idx_shots_job_status ON shots(job_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_storyboards_created_at ON storyboards(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_storyboard_shots_storyboard_id ON storyboard_shots(storyboard_id)",
            "CREATE INDEX IF NOT EXISTS idx_storyboard_shots_storyboard_number ON storyboard_shots(storyboard_id, shot_number)",
            "CREATE INDEX IF NOT EXISTS idx_storyboard_generation_tasks_created_at ON storyboard_generation_tasks(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_dimensions_shot_id ON dimensions(shot_id)",
            "CREATE INDEX IF NOT EXISTS idx_transcript_segments_job_id ON transcript_segments(job_id)",
        ):
            await conn.execute(text(statement))

        from backend.config import MOONSHOT_API_KEY, MOONSHOT_MODEL, MOONSHOT_BASE_URL
        defaults = [
            ("analysis_api_key", MOONSHOT_API_KEY, "分析引擎 API 密钥"),
            ("analysis_model", MOONSHOT_MODEL, "分析引擎模型名称"),
            ("analysis_base_url", MOONSHOT_BASE_URL, "分析引擎接口地址"),
            ("storyboard_api_key", MOONSHOT_API_KEY, "分镜引擎 API 密钥"),
            ("storyboard_model", MOONSHOT_MODEL, "分镜引擎模型名称"),
            ("storyboard_base_url", MOONSHOT_BASE_URL, "分镜引擎接口地址"),
        ]
        for key, value, description in defaults:
            await conn.execute(
                text(
                    "INSERT OR IGNORE INTO system_settings (key, value, description, updated_at) "
                    "VALUES (:key, :value, :description, CURRENT_TIMESTAMP)"
                ),
                {"key": key, "value": value, "description": description},
            )

    # Reset any stuck jobs (analyzing/preprocessing -> failed)
    from backend.models import Job, StoryboardGenerationTask, _now
    from sqlalchemy import update
    async with engine.begin() as conn:
        await conn.execute(
            update(Job)
            .where(Job.status.in_(["preprocessing", "preprocessing_done", "analyzing", "cancelling"]))
            .values(status="failed", error_message=INTERRUPTED_JOB_MESSAGE, updated_at=_now())
        )
        await conn.execute(
            update(StoryboardGenerationTask)
            .where(StoryboardGenerationTask.status.in_(["queued", "collecting", "generating", "saving"]))
            .values(
                status="failed",
                message="上一次分镜生成在应用或后端退出时中断，请重新生成。",
                error_message="上一次分镜生成在应用或后端退出时中断，请重新生成。",
                updated_at=_now(),
            )
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
