import os
import logging
import re
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
CURRENT_SCHEMA_VERSION = 2


_LOG_SECRET_PATTERNS = (
    (re.compile(r"([?&]token=)[^&\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(X-(?:Framebench|Film-Master)-Token:\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
)


def _redact_log_value(value):
    if not isinstance(value, str):
        return value
    for pattern, replacement in _LOG_SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


class _SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_log_value(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_log_value(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact_log_value(value) for key, value in record.args.items()}
        return True


def _install_log_redaction():
    for logger_name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        if not any(isinstance(f, _SecretRedactionFilter) for f in logger.filters):
            logger.addFilter(_SecretRedactionFilter())


_install_log_redaction()


async def _migrate_schema_v2(conn):
    from sqlalchemy import text

    for table_name in ("storyboards", "storyboard_generation_tasks"):
        columns = await conn.execute(text(f"PRAGMA table_info({table_name})"))
        column_names = {row[1] for row in columns.fetchall()}
        if "reference_shot_ids" not in column_names:
            await conn.execute(text(
                f"ALTER TABLE {table_name} "
                "ADD COLUMN reference_shot_ids TEXT NOT NULL DEFAULT '[]'"
            ))


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_data_root()

    # Startup: create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy import bindparam, text
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "version INTEGER NOT NULL, "
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        ))
        await conn.execute(text(
            "INSERT OR IGNORE INTO schema_version (id, version, updated_at) "
            "VALUES (1, 0, CURRENT_TIMESTAMP)"
        ))
        version_result = await conn.execute(text("SELECT version FROM schema_version WHERE id = 1"))
        schema_version = int(version_result.scalar_one_or_none() or 0)

        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_category ON jobs(category)",
            "CREATE INDEX IF NOT EXISTS idx_shots_job_status ON shots(job_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_storyboards_created_at ON storyboards(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_storyboard_shots_storyboard_id ON storyboard_shots(storyboard_id)",
            "CREATE INDEX IF NOT EXISTS idx_storyboard_shots_storyboard_number ON storyboard_shots(storyboard_id, shot_number)",
            "CREATE INDEX IF NOT EXISTS idx_storyboard_generation_tasks_created_at ON storyboard_generation_tasks(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_reference_board_created_at ON reference_board_items(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_dimensions_shot_id ON dimensions(shot_id)",
            "CREATE INDEX IF NOT EXISTS idx_transcript_segments_job_id ON transcript_segments(job_id)",
        ):
            await conn.execute(text(statement))

        if schema_version < 1:
            storyboard_columns = await conn.execute(text("PRAGMA table_info(storyboard_shots)"))
            storyboard_column_names = {row[1] for row in storyboard_columns.fetchall()}
            if "image_url" not in storyboard_column_names:
                await conn.execute(text("ALTER TABLE storyboard_shots ADD COLUMN image_url TEXT"))
            if "image_status" not in storyboard_column_names:
                await conn.execute(text("ALTER TABLE storyboard_shots ADD COLUMN image_status TEXT"))
            if "image_error" not in storyboard_column_names:
                await conn.execute(text("ALTER TABLE storyboard_shots ADD COLUMN image_error TEXT"))
            if "image_updated_at" not in storyboard_column_names:
                await conn.execute(text("ALTER TABLE storyboard_shots ADD COLUMN image_updated_at DATETIME"))

            job_columns = await conn.execute(text("PRAGMA table_info(jobs)"))
            job_column_names = {row[1] for row in job_columns.fetchall()}
            if "deleted_at" not in job_column_names:
                await conn.execute(text("ALTER TABLE jobs ADD COLUMN deleted_at DATETIME"))

        if schema_version < 2:
            await _migrate_schema_v2(conn)

        from backend.config import (
            STEPFUN_API_KEY,
            STEPFUN_BASE_URL,
            STEPFUN_IMAGE_BASE_URL,
            STEPFUN_IMAGE_MODEL,
            STEPFUN_TEXT_MODEL,
        )
        defaults = [
            ("analysis_api_key", STEPFUN_API_KEY, "分析引擎 API 密钥"),
            ("analysis_model", STEPFUN_TEXT_MODEL, "分析引擎模型名称"),
            ("analysis_base_url", STEPFUN_BASE_URL, "分析引擎接口地址"),
            ("storyboard_api_key", STEPFUN_API_KEY, "分镜引擎 API 密钥"),
            ("storyboard_model", STEPFUN_TEXT_MODEL, "分镜引擎模型名称"),
            ("storyboard_base_url", STEPFUN_BASE_URL, "分镜引擎接口地址"),
            ("image_api_key", STEPFUN_API_KEY, "分镜图生成 API 密钥"),
            ("image_model", STEPFUN_IMAGE_MODEL, "分镜图生成模型名称"),
            ("image_base_url", STEPFUN_IMAGE_BASE_URL, "分镜图生成接口地址"),
            ("whisper_model", "base", "本地 Whisper 转写模型"),
        ]
        for key, value, description in defaults:
            await conn.execute(
                text(
                    "INSERT OR IGNORE INTO system_settings (key, value, description, updated_at) "
                    "VALUES (:key, :value, :description, CURRENT_TIMESTAMP)"
                ),
                {"key": key, "value": value, "description": description},
            )
        legacy_setting_updates = [
            ("analysis_model", STEPFUN_TEXT_MODEL, ("", "kimi-k2.6")),
            ("storyboard_model", STEPFUN_TEXT_MODEL, ("", "kimi-k2.6", "deepseek-v4-pro")),
            ("analysis_base_url", STEPFUN_BASE_URL, ("", "https://api.moonshot.cn/v1")),
            (
                "storyboard_base_url",
                STEPFUN_BASE_URL,
                ("", "https://api.moonshot.cn/v1", "https://api.deepseek.com/v1"),
            ),
        ]
        for key, value, legacy_values in legacy_setting_updates:
            await conn.execute(
                text(
                    "UPDATE system_settings SET value = :value, updated_at = CURRENT_TIMESTAMP "
                    "WHERE key = :key AND (value IS NULL OR value IN :legacy_values)"
                ).bindparams(bindparam("legacy_values", expanding=True)),
                {"key": key, "value": value, "legacy_values": legacy_values},
            )
        from backend.config import SECRET_SETTING_KEYS
        from backend.services.secret_store import KEYCHAIN_MARKER, store_secret_value
        for key in SECRET_SETTING_KEYS:
            result = await conn.execute(
                text("SELECT value FROM system_settings WHERE key = :key"),
                {"key": key},
            )
            value = result.scalar_one_or_none()
            if value and value != KEYCHAIN_MARKER:
                stored_value = store_secret_value(key, value)
                if stored_value != value:
                    await conn.execute(
                        text(
                            "UPDATE system_settings SET value = :value, updated_at = CURRENT_TIMESTAMP "
                            "WHERE key = :key"
                        ),
                        {"key": key, "value": stored_value},
                    )
        await conn.execute(
            text("UPDATE schema_version SET version = :version, updated_at = CURRENT_TIMESTAMP WHERE id = 1"),
            {"version": max(schema_version, CURRENT_SCHEMA_VERSION)},
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
