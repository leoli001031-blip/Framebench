import asyncio
import json
import os
import shutil
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional
from urllib.parse import urlparse
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Request, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from backend.database import get_db, AsyncSessionLocal
from backend.models import (
    Job, Shot, Dimension, TranscriptSegment, Storyboard, StoryboardShot,
    StoryboardGenerationTask, SystemSetting, ReferenceBoardItem, _now,
)
from backend.schemas import (
    JobListResponse, JobProgressResponse, JobResponse, JobDetailResponse,
    JobWithShotsResponse, ShotProgressResponse,
    JobShotsPageResponse,
    UploadResponse, StartResponse, DeleteResponse,
    UpdateJobRequest, RenameCategoryRequest, GenerateStoryboardRequest, StoryboardResponse,
    CategoryListResponse, StoryboardHistoryItem, StoryboardDetailResponse,
    StoryboardGenerationTaskResponse, SystemSettingResponse, UpdateSettingRequest, ConnectivityTestRequest,
    DataDiagnosticsResponse, DuplicateFilenameResponse, LegacyDataRootResponse,
    OrphanJobDirResponse, ArchiveOrphanJobDirResponse, StoryboardShotDetail, ImageConnectivityResponse,
    TokenUsageSummaryResponse,
    DimensionResponse, ReferenceBoardItemResponse, ReferenceBoardListResponse,
)
from backend.config import (
    DATA_ROOT, DB_PATH, JOBS_DIR, MAX_VIDEO_SIZE_MB,
    STORYBOARD_GENERATION_CONCURRENCY, known_data_roots,
)
from backend.services.job_manager import job_manager

router = APIRouter(prefix="/api")

SECRET_SETTING_KEYS = {"analysis_api_key", "storyboard_api_key", "image_api_key", "moonshot_api_key"}
MASK_PREFIX = "••••••••"
WHISPER_MODELS = {"tiny", "base", "small", "medium", "large"}
SETTING_MODEL_KEYS = {"analysis_model", "storyboard_model", "image_model"}
SETTING_URL_KEYS = {"analysis_base_url", "storyboard_base_url", "image_base_url"}
_storyboard_generation_tasks: set[asyncio.Task] = set()
_storyboard_generation_semaphore: asyncio.Semaphore | None = None
_storyboard_generation_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_storyboard_generation_semaphore() -> asyncio.Semaphore:
    global _storyboard_generation_semaphore, _storyboard_generation_semaphore_loop
    loop = asyncio.get_running_loop()
    if _storyboard_generation_semaphore is None or _storyboard_generation_semaphore_loop is not loop:
        _storyboard_generation_semaphore = asyncio.Semaphore(max(1, STORYBOARD_GENERATION_CONCURRENCY))
        _storyboard_generation_semaphore_loop = loop
    return _storyboard_generation_semaphore


def _read_report(report_path: str) -> str:
    """Read report file (blocking I/O, intended for asyncio.to_thread)."""
    with open(report_path, "r") as f:
        return f.read()


def _read_log_tail(log_path: str, max_bytes: int) -> tuple[bytes, bool]:
    size = os.path.getsize(log_path)
    with open(log_path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        return f.read(), size > max_bytes


def _iter_file_range(file_path: str, start: int, end: int, chunk_size: int = 1024 * 1024):
    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    if not range_header.startswith("bytes="):
        raise ValueError("Only byte ranges are supported")

    range_value = range_header.removeprefix("bytes=").split(",", 1)[0].strip()
    start_text, _, end_text = range_value.partition("-")

    if start_text == "":
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError("Invalid suffix range")
        return max(file_size - suffix_length, 0), file_size - 1

    start = int(start_text)
    end = int(end_text) if end_text else file_size - 1
    if start < 0 or end < start or start >= file_size:
        raise ValueError("Invalid byte range")
    return start, min(end, file_size - 1)


def _get_playback_video_path(job_id: str, original_path: str) -> str:
    playback_path = os.path.join(JOBS_DIR, job_id, "playback.mp4")
    if os.path.exists(playback_path) and os.path.getsize(playback_path) > 0:
        return playback_path
    return original_path


def validate_job_id(job_id: str) -> str:
    """Validate that job_id is a UUID to prevent path traversal."""
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(400, "Invalid job ID format")
    return job_id


def _mask_secret(value: Optional[str]) -> str:
    if not value:
        return ""
    from backend.services.secret_store import is_keychain_marker
    if is_keychain_marker(value):
        return MASK_PREFIX
    return f"{MASK_PREFIX}{value[-4:]}" if len(value) > 4 else MASK_PREFIX


def _is_masked_secret(value: str) -> bool:
    return value.startswith(MASK_PREFIX)


def _validated_setting_value(key: str, value: str) -> str:
    if key == "whisper_model":
        normalized = value.strip().lower()
        if normalized not in WHISPER_MODELS:
            allowed = "、".join(sorted(WHISPER_MODELS))
            raise HTTPException(400, f"Whisper 模型仅支持：{allowed}")
        return normalized

    if key in SETTING_MODEL_KEYS:
        normalized = value.strip()
        if not normalized:
            raise HTTPException(400, "模型标识不能为空")
        return normalized

    if key in SETTING_URL_KEYS:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(400, "接口地址必须是有效的 HTTP 或 HTTPS URL")
        if parsed.username or parsed.password:
            raise HTTPException(400, "接口地址不能包含用户名或密码")
        return normalized

    return value


def _validated_connectivity_value(value: Optional[str], label: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise HTTPException(400, f"{label}不能为空")
    return normalized


def _validated_connectivity_url(value: Optional[str]) -> str:
    normalized = _validated_connectivity_value(value, "接口地址").rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(400, "接口地址必须是有效的 HTTP 或 HTTPS URL")
    if parsed.username or parsed.password:
        raise HTTPException(400, "接口地址不能包含用户名或密码")
    return normalized


def _serialize_setting(setting: SystemSetting) -> SystemSettingResponse:
    is_secret = setting.key in SECRET_SETTING_KEYS
    value = _mask_secret(setting.value) if is_secret else setting.value
    return SystemSettingResponse(
        key=setting.key,
        value=value,
        description=setting.description,
        is_secret=is_secret,
        updated_at=setting.updated_at,
    )


def _serialize_storyboard_task(task: StoryboardGenerationTask) -> StoryboardGenerationTaskResponse:
    return StoryboardGenerationTaskResponse(
        id=task.id,
        brief=task.brief,
        reference_job_ids=json.loads(task.reference_job_ids or "[]"),
        reference_shot_ids=json.loads(task.reference_shot_ids or "[]"),
        target_duration_sec=task.target_duration_sec,
        status=task.status,
        progress=task.progress or 0.0,
        message=task.message,
        storyboard_id=task.storyboard_id,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _serialize_reference_board_item(item: ReferenceBoardItem) -> ReferenceBoardItemResponse:
    shot = item.shot
    return ReferenceBoardItemResponse(
        shot_id=shot.id,
        job_id=shot.job_id,
        job_filename=shot.job.filename,
        job_category=shot.job.category,
        shot_number=shot.shot_number,
        start_time_sec=shot.start_time_sec,
        end_time_sec=shot.end_time_sec,
        keyframe_paths=shot.keyframe_paths,
        status=shot.status,
        overall_notes=shot.overall_notes,
        analysis_text=shot.analysis_text,
        techniques_json=shot.techniques_json,
        dimensions=[DimensionResponse.model_validate(dimension) for dimension in shot.dimensions],
        created_at=item.created_at,
    )


def _safe_techniques(value: Optional[str]) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _reference_analysis_text(shot: Shot) -> str:
    analysis = (shot.analysis_text or shot.overall_notes or "").strip()
    dimension_text = "; ".join(
        f"{dimension.dimension_name}: "
        f"{dimension.label or '-'}"
        f"{f' ({dimension.notes})' if dimension.notes else ''}"
        for dimension in shot.dimensions
    )
    if analysis and dimension_text:
        return f"{analysis}\n维度: {dimension_text}"
    return analysis or dimension_text


async def _resolve_reference_job_ids(db: AsyncSession, shot_ids: list[int]) -> list[str]:
    result = await db.execute(
        select(Shot.id, Shot.job_id)
        .join(Job, Job.id == Shot.job_id)
        .where(Shot.id.in_(shot_ids), Job.deleted_at.is_(None))
    )
    job_by_shot = {row.id: row.job_id for row in result.all()}
    missing = [shot_id for shot_id in shot_ids if shot_id not in job_by_shot]
    if missing:
        raise HTTPException(404, f"Reference shots not found: {missing[:5]}")
    return list(dict.fromkeys(job_by_shot[shot_id] for shot_id in shot_ids))


async def _collect_storyboard_references(
    db: AsyncSession,
    reference_job_ids: list[str],
    reference_shot_ids: list[int],
) -> list[dict]:
    if reference_shot_ids:
        result = await db.execute(
            select(Shot)
            .join(Job, Job.id == Shot.job_id)
            .where(Shot.id.in_(reference_shot_ids), Job.deleted_at.is_(None))
            .options(selectinload(Shot.dimensions), selectinload(Shot.job))
        )
        shot_by_id = {shot.id: shot for shot in result.scalars().all()}
        missing = [shot_id for shot_id in reference_shot_ids if shot_id not in shot_by_id]
        if missing:
            raise HTTPException(404, f"Reference shots not found: {missing[:5]}")

        references_by_job: dict[str, dict] = {}
        for selection_order, shot_id in enumerate(reference_shot_ids):
            shot = shot_by_id[shot_id]
            job = shot.job
            reference = references_by_job.setdefault(job.id, {
                "filename": job.filename,
                "category": job.category or "",
                "overview_text": job.overview_text or "",
                "shots": [],
                "all_techniques": [],
                "shots_are_selected": True,
            })
            techniques = _safe_techniques(shot.techniques_json)
            reference["shots"].append({
                "shot_id": shot.id,
                "shot_number": shot.shot_number,
                "duration_sec": shot.end_time_sec - shot.start_time_sec,
                "analysis_text": _reference_analysis_text(shot),
                "techniques": techniques,
                "selection_order": selection_order,
                "source_filename": job.filename,
            })
            reference["all_techniques"].extend(techniques)
        return list(references_by_job.values())

    result = await db.execute(
        select(Job)
        .where(Job.id.in_(reference_job_ids), Job.deleted_at.is_(None))
        .options(selectinload(Job.shots).selectinload(Shot.dimensions))
    )
    job_by_id = {job.id: job for job in result.scalars().all()}
    missing = [job_id for job_id in reference_job_ids if job_id not in job_by_id]
    if missing:
        raise HTTPException(404, f"Reference jobs not found: {missing[:5]}")

    references = []
    for job_id in reference_job_ids:
        job = job_by_id[job_id]
        shot_data = []
        all_techniques = []
        for shot in sorted(job.shots, key=lambda item: item.shot_number):
            analysis_text = _reference_analysis_text(shot)
            if not analysis_text:
                continue
            techniques = _safe_techniques(shot.techniques_json)
            shot_data.append({
                "shot_number": shot.shot_number,
                "duration_sec": shot.end_time_sec - shot.start_time_sec,
                "analysis_text": analysis_text,
                "techniques": techniques,
            })
            all_techniques.extend(techniques)
        references.append({
            "filename": job.filename,
            "category": job.category or "",
            "overview_text": job.overview_text or "",
            "shots": shot_data,
            "all_techniques": all_techniques,
        })
    return references


def _safe_client_task_id(client_task_id: Optional[str]) -> str:
    if client_task_id:
        try:
            return str(uuid.UUID(client_task_id))
        except ValueError:
            pass
    return str(uuid.uuid4())


def _storyboard_progress_for_message(message: str) -> tuple[str, float]:
    if "解析" in message:
        return "saving", 0.86
    if "调用 AI" in message or "生成分镜" in message:
        return "generating", 0.58
    if "已收集" in message:
        return "collecting", 0.28
    if "收集" in message:
        return "collecting", 0.12
    return "generating", 0.45


def _is_uuid_text(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _directory_stats(path: str) -> tuple[int, int, Optional[datetime]]:
    file_count = 0
    size_bytes = 0
    modified_ts = 0.0
    for root, _, files in os.walk(path):
        for name in files:
            file_count += 1
            full_path = os.path.join(root, name)
            try:
                stat = os.stat(full_path)
            except OSError:
                continue
            size_bytes += stat.st_size
            modified_ts = max(modified_ts, stat.st_mtime)
    modified_at = datetime.fromtimestamp(modified_ts, timezone.utc) if modified_ts else None
    return file_count, size_bytes, modified_at


def _find_job_dirs() -> list[str]:
    if not os.path.isdir(JOBS_DIR):
        return []
    dirs = []
    with os.scandir(JOBS_DIR) as entries:
        for entry in entries:
            if entry.is_dir() and _is_uuid_text(entry.name):
                dirs.append(entry.name)
    return sorted(dirs)


def _archive_orphan_dir(orphan_id: str) -> str:
    validate_job_id(orphan_id)
    source = os.path.join(JOBS_DIR, orphan_id)
    jobs_root = os.path.abspath(JOBS_DIR)
    source_abs = os.path.abspath(source)
    if os.path.dirname(source_abs) != jobs_root or not os.path.isdir(source_abs):
        raise HTTPException(404, "Orphan job directory not found")

    archive_root = os.path.join(os.path.dirname(JOBS_DIR), "_orphans")
    os.makedirs(archive_root, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    target = os.path.join(archive_root, f"{orphan_id}-{timestamp}")
    suffix = 1
    while os.path.exists(target):
        suffix += 1
        target = os.path.join(archive_root, f"{orphan_id}-{timestamp}-{suffix}")
    shutil.move(source_abs, target)
    return target


def _legacy_root_info(root: str) -> LegacyDataRootResponse:
    root_abs = os.path.abspath(root)
    db_exists = (
        os.path.exists(os.path.join(root_abs, "film_master.db"))
        or os.path.exists(os.path.join(root_abs, "data", "film_master.db"))
    )
    jobs_dir_exists = (
        os.path.isdir(os.path.join(root_abs, "jobs"))
        or os.path.isdir(os.path.join(root_abs, "data", "jobs"))
    )
    return LegacyDataRootResponse(path=root_abs, db_exists=db_exists, jobs_dir_exists=jobs_dir_exists)


async def _current_schema_version(db: AsyncSession) -> int:
    try:
        result = await db.execute(text("SELECT version FROM schema_version WHERE id = 1"))
        return int(result.scalar_one_or_none() or 0)
    except Exception:
        return 0


def _redact_log_text(text_value: str) -> str:
    import re
    redacted = re.sub(r"([?&]token=)[^&\s\"]+", r"\1[REDACTED]", text_value)
    redacted = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=\-]{8,}", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(X-Framebench-Token:\s*)[A-Za-z0-9._~+/=\-]+", r"\1[REDACTED]", redacted, flags=re.IGNORECASE)
    redacted = re.sub(r"(X-Film-Master-Token:\s*)[A-Za-z0-9._~+/=\-]+", r"\1[REDACTED]", redacted, flags=re.IGNORECASE)
    return redacted


async def _generate_and_store_storyboard_image(
    *,
    storyboard_id: str,
    shot_number: int,
    prompt: str,
) -> StoryboardShotDetail:
    from backend.services.storyboard_images import (
        StoryboardImageError,
        generate_storyboard_shot_image,
        get_storyboard_image_config,
    )

    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise HTTPException(400, "分镜没有可用的生图提示词")

    image_config = await get_storyboard_image_config()
    if not image_config.api_key:
        raise HTTPException(400, "分镜图生成 API 密钥未配置")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(StoryboardShot).where(
                StoryboardShot.storyboard_id == storyboard_id,
                StoryboardShot.shot_number == shot_number,
            )
        )
        shot = result.scalar_one_or_none()
        if not shot:
            raise HTTPException(404, "Storyboard shot not found")
        shot.image_status = "generating"
        shot.image_error = None
        shot.image_updated_at = _now()
        await db.commit()

    try:
        generated = await generate_storyboard_shot_image(
            storyboard_id=storyboard_id,
            shot_number=shot_number,
            prompt=clean_prompt,
            config=image_config,
        )
    except (StoryboardImageError, httpx.HTTPError) as exc:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(StoryboardShot).where(
                    StoryboardShot.storyboard_id == storyboard_id,
                    StoryboardShot.shot_number == shot_number,
                )
            )
            shot = result.scalar_one_or_none()
            if shot:
                shot.image_status = "failed"
                shot.image_error = str(exc)[:300]
                shot.image_updated_at = _now()
                await db.commit()
        raise HTTPException(502, f"分镜图生成失败: {str(exc)[:200]}") from exc

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(StoryboardShot).where(
                StoryboardShot.storyboard_id == storyboard_id,
                StoryboardShot.shot_number == shot_number,
            )
        )
        shot = result.scalar_one_or_none()
        if not shot:
            raise HTTPException(404, "Storyboard shot not found")
        shot.image_url = generated.image_url if generated else None
        shot.image_status = "completed" if generated else "skipped"
        shot.image_error = None
        shot.image_updated_at = _now()
        await db.commit()
        await db.refresh(shot)
        return StoryboardShotDetail.model_validate(shot)


async def _update_storyboard_task(task_id: str, **fields):
    async with AsyncSessionLocal() as db:
        task = await db.get(StoryboardGenerationTask, task_id)
        if not task:
            return
        for key, value in fields.items():
            setattr(task, key, value)
        await db.commit()


@router.post("/upload", response_model=UploadResponse)
async def upload_video(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith((".mp4", ".mov", ".mkv", ".avi", ".webm")):
        raise HTTPException(400, "Unsupported video format. Use mp4, mov, mkv, avi, or webm.")

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    video_path = os.path.join(job_dir, "original.mp4")
    tmp_video_path = os.path.join(job_dir, "original.uploading")
    max_bytes = MAX_VIDEO_SIZE_MB * 1024 * 1024
    total = 0
    try:
        # Stream chunks directly to disk to avoid OOM on large videos
        with open(tmp_video_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(400, f"File too large ({total / (1024*1024):.0f}MB). Max: {MAX_VIDEO_SIZE_MB}MB")
                await asyncio.to_thread(f.write, chunk)
        os.replace(tmp_video_path, video_path)

        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            job = Job(
                id=job_id,
                filename=file.filename,
                video_path=video_path,
                status="pending",
            )
            db.add(job)
            await db.commit()
    except BaseException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    return UploadResponse(job_id=job_id, filename=file.filename, status="pending")


@router.post("/jobs/{job_id}/start", response_model=StartResponse)
async def start_job(job_id: str):
    validate_job_id(job_id)
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(404, "Job not found")
        if job.status not in ("pending", "failed", "partial_completed"):
            raise HTTPException(400, f"Cannot start job in status: {job.status}")

    if not job_manager.start(job_id):
        raise HTTPException(409, "Job is already running")
    return StartResponse(job_id=job_id, status="preprocessing")


@router.post("/jobs/{job_id}/cancel", response_model=StartResponse)
async def cancel_job(job_id: str):
    validate_job_id(job_id)
    accepted = await job_manager.request_cancel(job_id)

    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(404, "Job not found")

        if accepted:
            if job.status == "failed":
                return StartResponse(job_id=job_id, status="failed")
            if job_manager.is_running(job_id):
                update_result = await db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status != "failed")
                    .values(status="cancelling", error_message="Cancellation requested")
                )
                await db.commit()
                if update_result.rowcount:
                    return StartResponse(job_id=job_id, status="cancelling")
            await db.refresh(job)
            if job.status == "failed":
                return StartResponse(job_id=job_id, status="failed")
            raise HTTPException(409, f"Job already finished in status: {job.status}")

        if job.status in ("preprocessing", "preprocessing_done", "analyzing", "cancelling"):
            job.status = "failed"
            job.error_message = "Cancelled by user"
            await db.commit()
            return StartResponse(job_id=job_id, status="failed")

        raise HTTPException(409, f"Cannot cancel job in status: {job.status}")


@router.get("/jobs", response_model=list[JobListResponse])
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_deleted: Annotated[bool, Query()] = False,
    only_deleted: Annotated[bool, Query()] = False,
):
    query = (
        select(
            Job.id,
            Job.filename,
            Job.status,
            Job.progress,
            Job.total_shots,
            Job.duration_sec,
            Job.error_message,
            Job.category,
            Job.created_at,
            Job.updated_at,
            Job.deleted_at,
        )
    )
    if only_deleted:
        query = query.where(Job.deleted_at != None)
    elif not include_deleted:
        query = query.where(Job.deleted_at == None)

    result = await db.execute(
        query.order_by(Job.created_at.desc()).limit(limit).offset(offset)
    )
    return [JobListResponse(**row._mapping) for row in result.all()]


@router.get("/jobs/{job_id}", response_model=JobWithShotsResponse)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    validate_job_id(job_id)
    result = await db.execute(
        select(Job)
        .where(Job.id == job_id, Job.deleted_at == None)
        .options(selectinload(Job.shots).selectinload(Shot.dimensions), selectinload(Job.transcript_segments))
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")

    return job


@router.get("/jobs/{job_id}/summary", response_model=JobResponse)
async def get_job_summary(job_id: str, db: AsyncSession = Depends(get_db)):
    validate_job_id(job_id)
    result = await db.execute(
        select(
            Job.id,
            Job.filename,
            Job.status,
            Job.progress,
            Job.total_shots,
            Job.duration_sec,
            Job.error_message,
            Job.category,
            Job.overview_text,
            Job.created_at,
            Job.updated_at,
            Job.deleted_at,
        )
        .where(Job.id == job_id, Job.deleted_at == None)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(404, "Job not found")
    return JobResponse(**row._mapping)


@router.get("/jobs/{job_id}/shots", response_model=JobShotsPageResponse)
async def get_job_shots(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=500)] = 80,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    validate_job_id(job_id)
    job_exists = await db.scalar(select(func.count(Job.id)).where(Job.id == job_id, Job.deleted_at == None))
    if not job_exists:
        raise HTTPException(404, "Job not found")

    shots_total = int(await db.scalar(select(func.count(Shot.id)).where(Shot.job_id == job_id)) or 0)
    result = await db.execute(
        select(Shot)
        .where(Shot.job_id == job_id)
        .options(selectinload(Shot.dimensions))
        .order_by(Shot.shot_number)
        .limit(limit)
        .offset(offset)
    )
    shots = result.scalars().all()
    return JobShotsPageResponse(
        shots_total=shots_total,
        shot_offset=offset,
        shot_limit=limit,
        shots_returned=len(shots),
        shots_truncated=offset + len(shots) < shots_total,
        shots=shots,
    )


@router.get("/jobs/{job_id}/progress", response_model=JobProgressResponse)
async def get_job_progress(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    include_shots: Annotated[bool, Query()] = False,
    shot_limit: Annotated[int, Query(ge=0, le=500)] = 20,
    shot_offset: Annotated[int, Query(ge=0)] = 0,
):
    validate_job_id(job_id)
    result = await db.execute(
        select(
            Job.id,
            Job.filename,
            Job.status,
            Job.progress,
            Job.total_shots,
            Job.duration_sec,
            Job.error_message,
            Job.category,
            Job.created_at,
            Job.updated_at,
            Job.deleted_at,
        )
        .where(Job.id == job_id, Job.deleted_at == None)
    )
    job_row = result.one_or_none()
    if not job_row:
        raise HTTPException(404, "Job not found")

    counts_result = await db.execute(
        select(Shot.status, func.count(Shot.id))
        .where(Shot.job_id == job_id)
        .group_by(Shot.status)
    )
    status_counts = {status: count for status, count in counts_result.all()}
    counted_total = sum(status_counts.values())
    shots_total = int(job_row._mapping["total_shots"] or counted_total)
    completed_shots = int(status_counts.get("completed", 0))
    failed_shots = int(status_counts.get("failed", 0))
    pending_shots = max(0, shots_total - completed_shots - failed_shots)
    bounded_limit = max(0, shot_limit)

    shot_rows = []
    effective_offset = shot_offset if include_shots else 0
    if bounded_limit > 0:
        if include_shots:
            shots_query = (
                _progress_shot_select()
                .where(Shot.job_id == job_id)
                .order_by(Shot.shot_number)
                .limit(bounded_limit)
                .offset(effective_offset)
            )
            shots_result = await db.execute(shots_query)
            shot_rows = shots_result.all()
        else:
            active_result = await db.execute(
                _progress_shot_select()
                .where(Shot.job_id == job_id, Shot.status != "completed")
                .order_by(Shot.shot_number)
                .limit(bounded_limit)
            )
            shot_rows = active_result.all()
            if not shot_rows and completed_shots > 0:
                recent_result = await db.execute(
                    _progress_shot_select()
                    .where(Shot.job_id == job_id)
                    .order_by(Shot.shot_number.desc())
                    .limit(bounded_limit)
                )
                shot_rows = list(reversed(recent_result.all()))

    shots_returned = len(shot_rows)
    if include_shots:
        shots_truncated = shot_offset + shots_returned < counted_total
    else:
        shots_truncated = shots_returned < counted_total

    return JobProgressResponse(
        **job_row._mapping,
        shots_total=shots_total,
        completed_shots=completed_shots,
        failed_shots=failed_shots,
        pending_shots=pending_shots,
        shot_offset=effective_offset,
        shot_limit=bounded_limit,
        shots_returned=shots_returned,
        shots_truncated=shots_truncated,
        shots=[
            ShotProgressResponse(**row._mapping)
            for row in shot_rows
        ],
    )


def _progress_shot_select():
    return select(
        Shot.id,
        Shot.shot_number,
        Shot.start_time_sec,
        Shot.end_time_sec,
        Shot.keyframe_paths,
        Shot.status,
        Shot.analysis_text,
    )


@router.get("/jobs/{job_id}/video")
async def get_job_video(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    validate_job_id(job_id)
    result = await db.execute(select(Job).where(Job.id == job_id, Job.deleted_at == None))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    video_path = _get_playback_video_path(job_id, job.video_path)
    if not os.path.exists(video_path):
        raise HTTPException(404, "Video file not found")

    file_size = os.path.getsize(video_path)
    range_header = request.headers.get("range")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": "video/mp4",
        "Content-Disposition": "inline",
    }

    if range_header:
        try:
            start, end = _parse_range_header(range_header, file_size)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=416,
                detail="Invalid range",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        headers.update({
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(end - start + 1),
        })
        return StreamingResponse(
            _iter_file_range(video_path, start, end),
            status_code=206,
            headers=headers,
        )

    headers["Content-Length"] = str(file_size)
    return StreamingResponse(
        _iter_file_range(video_path, 0, file_size - 1),
        headers=headers,
    )


@router.delete("/jobs/{job_id}", response_model=DeleteResponse)
async def delete_job(job_id: str):
    validate_job_id(job_id)
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(404, "Job not found")

        # Running jobs are cancelled first; files are kept until processing stops.
        if job.status in ("preprocessing", "preprocessing_done", "analyzing", "cancelling"):
            accepted = await job_manager.request_cancel(job_id)
            if accepted and job_manager.is_running(job_id):
                await db.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.status.in_(("preprocessing", "preprocessing_done", "analyzing", "cancelling")),
                    )
                    .values(status="cancelling", error_message="Cancellation requested")
                )
                await db.commit()
            else:
                await db.refresh(job)
                if job.status in ("preprocessing", "preprocessing_done", "analyzing", "cancelling"):
                    job.status = "failed"
                    job.error_message = "Cancelled by user"
                    await db.commit()
            return DeleteResponse(deleted=False)

        now = _now()
        job.deleted_at = now
        job.updated_at = now
        await db.commit()

    return DeleteResponse(deleted=True)


@router.post("/jobs/{job_id}/restore", response_model=JobResponse)
async def restore_job(job_id: str):
    validate_job_id(job_id)
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(404, "Job not found")
        job.deleted_at = None
        job.updated_at = _now()
        await db.commit()
        await db.refresh(job)
        return job


@router.delete("/jobs/{job_id}/permanent", response_model=DeleteResponse)
async def permanently_delete_job(job_id: str):
    validate_job_id(job_id)
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(404, "Job not found")
        if job.deleted_at is None:
            raise HTTPException(400, "Move job to trash before permanent deletion")
        if job.status in ("preprocessing", "preprocessing_done", "analyzing", "cancelling"):
            raise HTTPException(400, "Cannot permanently delete a running job")

        job_dir = os.path.join(JOBS_DIR, job_id)
        if os.path.exists(job_dir):
            try:
                await asyncio.to_thread(shutil.rmtree, job_dir)
            except OSError as exc:
                raise HTTPException(500, f"Failed to delete local job files: {str(exc)[:200]}") from exc

        await db.delete(job)
        await db.commit()

    return DeleteResponse(deleted=True)


@router.get("/jobs/{job_id}/report")
async def get_report(job_id: str, format: str = "md", db: AsyncSession = Depends(get_db)):
    validate_job_id(job_id)
    job_exists = await db.scalar(select(func.count(Job.id)).where(Job.id == job_id, Job.deleted_at == None))
    if not job_exists:
        raise HTTPException(404, "Job not found")
    report_path = os.path.join(JOBS_DIR, job_id, "report.md")
    if not os.path.exists(report_path):
        from backend.services.report import build_report
        try:
            await build_report(job_id)
        except Exception as e:
            raise HTTPException(500, f"Report generation failed: {str(e)[:200]}")
        if not os.path.exists(report_path):
            raise HTTPException(404, "Report not available yet")

    content = await asyncio.to_thread(_read_report, report_path)

    if format == "json":
        return {"content": content, "format": "markdown"}
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content, media_type="text/markdown")


@router.get("/jobs/{job_id}/token-usage", response_model=TokenUsageSummaryResponse)
async def get_job_token_usage(job_id: str, db: AsyncSession = Depends(get_db)):
    validate_job_id(job_id)
    job_exists = await db.scalar(select(func.count(Job.id)).where(Job.id == job_id, Job.deleted_at == None))
    if not job_exists:
        raise HTTPException(404, "Job not found")
    from backend.services.token_usage import summarize_token_usage
    return await asyncio.to_thread(summarize_token_usage, job_id)


@router.put("/jobs/{job_id}", response_model=JobResponse)
async def update_job(job_id: str, req: UpdateJobRequest):
    validate_job_id(job_id)
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id, Job.deleted_at == None))
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(404, "Job not found")
        if req.category is not None:
            job.category = req.category
        if req.filename is not None:
            job.filename = req.filename
        await db.commit()
        await db.refresh(job)
        return job


@router.get("/categories", response_model=CategoryListResponse)
async def list_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Job.category)
        .where(Job.category != None, Job.deleted_at == None)
        .distinct()
        .order_by(Job.category)
    )
    cats = [row[0] for row in result.all() if row[0]]
    return CategoryListResponse(categories=cats)


@router.post("/categories/rename")
async def rename_category(req: RenameCategoryRequest, db: AsyncSession = Depends(get_db)):
    old_name = req.old_name.strip()
    new_name = req.new_name.strip()
    if not old_name or not new_name:
        raise HTTPException(400, "分类名不能为空")
    new_value = None if new_name == "未分类" else new_name
    result = await db.execute(
        update(Job)
        .where(Job.category == old_name, Job.deleted_at == None)
        .values(category=new_value, updated_at=_now())
    )
    await db.commit()
    return {"updated": result.rowcount or 0}


@router.get("/data-diagnostics", response_model=DataDiagnosticsResponse)
async def get_data_diagnostics(db: AsyncSession = Depends(get_db)):
    active_jobs = int(await db.scalar(select(func.count(Job.id)).where(Job.deleted_at == None)) or 0)
    deleted_jobs = int(await db.scalar(select(func.count(Job.id)).where(Job.deleted_at != None)) or 0)
    schema_version = await _current_schema_version(db)

    all_ids_result = await db.execute(select(Job.id))
    known_job_ids = set(all_ids_result.scalars().all())
    disk_job_ids = await asyncio.to_thread(_find_job_dirs)

    orphan_dirs: list[OrphanJobDirResponse] = []
    orphan_ids = [dirname for dirname in disk_job_ids if dirname not in known_job_ids]
    orphan_stats = await asyncio.gather(*[
        asyncio.to_thread(_directory_stats, os.path.join(JOBS_DIR, dirname))
        for dirname in orphan_ids
    ])
    for dirname, (file_count, size_bytes, modified_at) in zip(orphan_ids, orphan_stats):
        path = os.path.join(JOBS_DIR, dirname)
        orphan_dirs.append(OrphanJobDirResponse(
            id=dirname,
            path=path,
            file_count=file_count,
            size_bytes=size_bytes,
            modified_at=modified_at,
            has_report=os.path.exists(os.path.join(path, "report.md")),
            has_original=os.path.exists(os.path.join(path, "original.mp4")),
            has_playback=os.path.exists(os.path.join(path, "playback.mp4")),
        ))

    active_rows_result = await db.execute(
        select(Job.id, Job.filename).where(Job.deleted_at == None).order_by(Job.filename)
    )
    by_filename: dict[str, list[str]] = {}
    for job_id, filename in active_rows_result.all():
        by_filename.setdefault(filename, []).append(job_id)
    duplicate_filenames = [
        DuplicateFilenameResponse(filename=filename, count=len(ids), job_ids=ids)
        for filename, ids in by_filename.items()
        if len(ids) > 1
    ]

    legacy_roots = [
        _legacy_root_info(root)
        for root in known_data_roots()
    ]

    return DataDiagnosticsResponse(
        data_root=os.path.abspath(DATA_ROOT),
        db_path=os.path.abspath(DB_PATH),
        jobs_dir=os.path.abspath(JOBS_DIR),
        logs_dir=os.path.abspath(os.path.join(DATA_ROOT, "logs")),
        schema_version=schema_version,
        active_jobs=active_jobs,
        deleted_jobs=deleted_jobs,
        disk_job_dirs=len(disk_job_ids),
        orphan_archive_dir=os.path.abspath(os.path.join(os.path.dirname(JOBS_DIR), "_orphans")),
        orphan_job_dirs=orphan_dirs,
        duplicate_filenames=duplicate_filenames,
        legacy_roots=legacy_roots,
    )


@router.post("/data-diagnostics/orphans/{orphan_id}/archive", response_model=ArchiveOrphanJobDirResponse)
async def archive_orphan_job_dir(orphan_id: str, db: AsyncSession = Depends(get_db)):
    validate_job_id(orphan_id)
    existing = await db.get(Job, orphan_id)
    if existing:
        raise HTTPException(409, "Job still exists in database")
    archived_path = await asyncio.to_thread(_archive_orphan_dir, orphan_id)
    return ArchiveOrphanJobDirResponse(archived=True, id=orphan_id, archived_path=archived_path)


@router.get("/diagnostics/logs/backend")
async def get_backend_log():
    log_path = os.path.join(DATA_ROOT, "logs", "backend.log")
    if not os.path.exists(log_path):
        raise HTTPException(404, "Backend log not found")
    max_bytes = 1024 * 1024
    raw, truncated = await asyncio.to_thread(_read_log_tail, log_path, max_bytes)
    text_value = raw.decode("utf-8", errors="replace")
    if truncated:
        text_value = "[showing last 1MB]\n" + text_value
    return PlainTextResponse(_redact_log_text(text_value), media_type="text/plain")


@router.get("/reference-board", response_model=ReferenceBoardListResponse)
async def list_reference_board(
    job_id: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: AsyncSession = Depends(get_db),
):
    conditions = [Job.deleted_at.is_(None)]
    if job_id:
        validate_job_id(job_id)
        conditions.append(Shot.job_id == job_id)

    total_result = await db.execute(
        select(func.count(ReferenceBoardItem.shot_id))
        .select_from(ReferenceBoardItem)
        .join(Shot, Shot.id == ReferenceBoardItem.shot_id)
        .join(Job, Job.id == Shot.job_id)
        .where(*conditions)
    )
    result = await db.execute(
        select(ReferenceBoardItem)
        .join(Shot, Shot.id == ReferenceBoardItem.shot_id)
        .join(Job, Job.id == Shot.job_id)
        .where(*conditions)
        .options(
            selectinload(ReferenceBoardItem.shot).selectinload(Shot.dimensions),
            selectinload(ReferenceBoardItem.shot).selectinload(Shot.job),
        )
        .order_by(ReferenceBoardItem.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = result.scalars().all()
    return ReferenceBoardListResponse(
        total=int(total_result.scalar_one() or 0),
        items=[_serialize_reference_board_item(item) for item in items],
    )


@router.put("/reference-board/shots/{shot_id}", response_model=ReferenceBoardItemResponse)
async def add_reference_board_shot(shot_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Shot)
        .join(Job, Job.id == Shot.job_id)
        .where(Shot.id == shot_id, Job.deleted_at.is_(None))
        .options(selectinload(Shot.dimensions), selectinload(Shot.job))
    )
    shot = result.scalar_one_or_none()
    if not shot:
        raise HTTPException(404, "Shot not found")

    item = await db.get(ReferenceBoardItem, shot_id)
    if item is None:
        item = ReferenceBoardItem(shot_id=shot_id)
        db.add(item)
        await db.commit()
        await db.refresh(item)
    item.shot = shot
    return _serialize_reference_board_item(item)


@router.delete("/reference-board/shots/{shot_id}", response_model=DeleteResponse)
async def remove_reference_board_shot(shot_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(ReferenceBoardItem, shot_id)
    if item is not None:
        await db.delete(item)
        await db.commit()
    return DeleteResponse(deleted=True)


@router.post("/generate-storyboard")
async def generate_storyboard(req: GenerateStoryboardRequest):
    if bool(req.reference_job_ids) == bool(req.reference_shot_ids):
        raise HTTPException(400, "Choose either reference jobs or reference shots")

    from backend.services.storyboard_generator import generate_storyboard as run_storyboard_generation

    queue: asyncio.Queue = asyncio.Queue()
    task_id = _safe_client_task_id(req.client_task_id)
    async with AsyncSessionLocal() as db:
        resolved_job_ids = (
            await _resolve_reference_job_ids(db, req.reference_shot_ids)
            if req.reference_shot_ids
            else req.reference_job_ids
        )
        if await db.get(StoryboardGenerationTask, task_id):
            raise HTTPException(409, "同一分镜任务已存在，请等待任务状态同步")
        task = StoryboardGenerationTask(
            id=task_id,
            brief=req.brief,
            reference_job_ids=json.dumps(resolved_job_ids),
            reference_shot_ids=json.dumps(req.reference_shot_ids),
            target_duration_sec=req.target_duration_sec,
            status="queued",
            progress=0.02,
            message="已加入生成队列",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        await queue.put({
            "event": "started",
            "data": {"task": _serialize_storyboard_task(task).model_dump(mode="json")},
        })

    last_persisted = {"status": "queued", "progress": 0.02, "at": time.monotonic()}

    async def publish_progress(message: str, status: str, progress: float, force: bool = False):
        now = time.monotonic()
        should_persist = (
            force
            or status != last_persisted["status"]
            or progress - float(last_persisted["progress"]) >= 0.05
            or now - float(last_persisted["at"]) >= 1.5
        )
        if should_persist:
            await _update_storyboard_task(
                task_id,
                status=status,
                progress=progress,
                message=message,
                error_message=None,
            )
            last_persisted.update({"status": status, "progress": progress, "at": now})
        await queue.put({
            "event": "progress",
            "data": {
                "task_id": task_id,
                "status": status,
                "progress": progress,
                "message": message,
            },
        })

    async def collect_and_generate():
        """Collect references from DB and run AI generation, pushing progress to queue."""
        semaphore = _get_storyboard_generation_semaphore()
        acquired = False
        try:
            await semaphore.acquire()
            acquired = True
            print(
                f"Storyboard generation started: jobs={len(resolved_job_ids)}, "
                f"shots={len(req.reference_shot_ids)}, "
                f"target_duration={req.target_duration_sec or 'unset'}"
            )
            await publish_progress("正在读取参考素材…", "collecting", 0.08, force=True)
            # Collect reference analyses
            async with AsyncSessionLocal() as db:
                references = await _collect_storyboard_references(
                    db,
                    resolved_job_ids if not req.reference_shot_ids else [],
                    req.reference_shot_ids,
                )

            async def on_progress(msg: str):
                status, progress = _storyboard_progress_for_message(msg)
                await publish_progress(msg, status, progress)

            result = await run_storyboard_generation(
                req.brief, references, req.target_duration_sec,
                progress_callback=on_progress,
                model_call_id=task_id,
            )

            # Save to history
            await publish_progress("正在保存分镜脚本…", "saving", 0.9, force=True)
            sb_id = str(uuid.uuid4())
            shots = result.get("shots", [])
            async with AsyncSessionLocal() as db:
                sb = Storyboard(
                    id=sb_id,
                    title=result["title"],
                    brief=req.brief,
                    full_notes=result.get("full_notes", ""),
                    total_duration_sec=result.get("total_duration_sec", 0),
                    reference_job_ids=json.dumps(resolved_job_ids),
                    reference_shot_ids=json.dumps(req.reference_shot_ids),
                )
                db.add(sb)
                for s in shots:
                    db.add(StoryboardShot(
                        storyboard_id=sb_id,
                        shot_number=s["shot_number"],
                        duration_sec=s["duration_sec"],
                        description=s["description"],
                        camera_movement=s.get("camera_movement", ""),
                        bgm_note=s.get("bgm_note", ""),
                        reference_from=s.get("reference_from", ""),
                        image_prompt=s.get("image_prompt", ""),
                        image_url=s.get("image_url"),
                        image_status="pending" if s.get("image_prompt") else "skipped",
                        image_error=None,
                        image_updated_at=_now(),
                    ))
                await db.commit()

            result["id"] = sb_id
            result["reference_job_ids"] = resolved_job_ids
            result["reference_shot_ids"] = req.reference_shot_ids
            completion_message = "分镜脚本已保存，未配置生图 API key，已跳过图片生成"
            image_client: httpx.AsyncClient | None = None
            try:
                from backend.services.storyboard_images import (
                    StoryboardImageError,
                    generate_storyboard_shot_image,
                    get_storyboard_image_config,
                )

                image_config = await get_storyboard_image_config() if req.generate_images else None
                if req.generate_images and image_config.api_key and shots:
                    image_client = httpx.AsyncClient(timeout=180)
                    image_failures = 0
                    for idx, shot in enumerate(shots, start=1):
                        await publish_progress(
                            f"正在生成分镜图 {idx}/{len(shots)}…",
                            "saving",
                            min(0.99, 0.92 + (idx - 1) / max(len(shots), 1) * 0.07),
                            force=True,
                        )
                        prompt = shot.get("image_prompt") or shot.get("description") or ""
                        try:
                            async with AsyncSessionLocal() as db:
                                await db.execute(
                                    update(StoryboardShot)
                                    .where(
                                        StoryboardShot.storyboard_id == sb_id,
                                        StoryboardShot.shot_number == int(shot["shot_number"]),
                                    )
                                    .values(image_status="generating", image_error=None, image_updated_at=_now())
                                )
                                await db.commit()
                            generated = await generate_storyboard_shot_image(
                                storyboard_id=sb_id,
                                shot_number=int(shot["shot_number"]),
                                prompt=prompt,
                                config=image_config,
                                client=image_client,
                            )
                        except (StoryboardImageError, httpx.HTTPError) as e:
                            image_failures += 1
                            shot["image_status"] = "failed"
                            shot["image_error"] = str(e)[:300]
                            async with AsyncSessionLocal() as db:
                                await db.execute(
                                    update(StoryboardShot)
                                    .where(
                                        StoryboardShot.storyboard_id == sb_id,
                                        StoryboardShot.shot_number == int(shot["shot_number"]),
                                    )
                                    .values(image_status="failed", image_error=str(e)[:300], image_updated_at=_now())
                                )
                                await db.commit()
                            print(f"Storyboard image generation failed: storyboard={sb_id}, shot={shot.get('shot_number')}, error={e}")
                            continue

                        if not generated:
                            shot["image_status"] = "skipped"
                            async with AsyncSessionLocal() as db:
                                await db.execute(
                                    update(StoryboardShot)
                                    .where(
                                        StoryboardShot.storyboard_id == sb_id,
                                        StoryboardShot.shot_number == int(shot["shot_number"]),
                                    )
                                    .values(image_status="skipped", image_updated_at=_now())
                                )
                                await db.commit()
                            continue
                        shot["image_url"] = generated.image_url
                        shot["image_status"] = "completed"
                        shot["image_error"] = None
                        async with AsyncSessionLocal() as db:
                            await db.execute(
                                update(StoryboardShot)
                                .where(
                                    StoryboardShot.storyboard_id == sb_id,
                                    StoryboardShot.shot_number == int(shot["shot_number"]),
                                )
                                .values(
                                    image_url=generated.image_url,
                                    image_status="completed",
                                    image_error=None,
                                    image_updated_at=_now(),
                                )
                            )
                            await db.commit()
                    if image_failures:
                        completion_message = f"分镜脚本已保存，{len(shots) - image_failures} 张分镜图已生成，{image_failures} 张失败"
                    else:
                        completion_message = "分镜脚本和分镜图已保存"
                elif not req.generate_images and shots:
                    completion_message = "快速分镜脚本已保存，可按镜头生成分镜图"
                    for shot in shots:
                        shot["image_status"] = "pending"
                elif shots:
                    for shot in shots:
                        shot["image_status"] = "skipped"
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(StoryboardShot)
                            .where(StoryboardShot.storyboard_id == sb_id)
                            .values(image_status="skipped", image_updated_at=_now())
                        )
                        await db.commit()
            except Exception as e:
                completion_message = "分镜脚本已保存，分镜图生成失败"
                for shot in shots:
                    if not shot.get("image_url"):
                        shot["image_status"] = "failed"
                        shot["image_error"] = str(e)[:300]
                async with AsyncSessionLocal() as db:
                    await db.execute(
                        update(StoryboardShot)
                        .where(
                            StoryboardShot.storyboard_id == sb_id,
                            StoryboardShot.image_url == None,
                        )
                        .values(image_status="failed", image_error=str(e)[:300], image_updated_at=_now())
                    )
                    await db.commit()
                print(f"Storyboard image generation setup failed: storyboard={sb_id}, error={e}")
            finally:
                if image_client is not None:
                    await image_client.aclose()

            await _update_storyboard_task(
                task_id,
                status="completed",
                progress=1.0,
                message=completion_message,
                storyboard_id=sb_id,
                error_message=None,
            )
            print(
                f"Storyboard generation saved: id={sb_id}, "
                f"title={result.get('title', '')[:80]}, shots={len(result.get('shots', []))}"
            )
            await queue.put({"event": "complete", "data": {"task_id": task_id, "result": result}})
        except RuntimeError as e:
            message = f"AI generation failed: {str(e)[:300]}"
            print(f"Storyboard generation failed: {e}")
            await _update_storyboard_task(
                task_id,
                status="failed",
                message=message,
                error_message=message,
            )
            await queue.put({"event": "error", "data": {"task_id": task_id, "message": message}})
        except Exception as e:
            message = str(e)[:300]
            traceback.print_exc()
            await _update_storyboard_task(
                task_id,
                status="failed",
                message=message,
                error_message=message,
            )
            await queue.put({"event": "error", "data": {"task_id": task_id, "message": message}})
        finally:
            if acquired:
                semaphore.release()
            await queue.put(None)  # Sentinel to close the stream

    # Start collection + generation in background
    generation_task = asyncio.create_task(collect_and_generate())
    _storyboard_generation_tasks.add(generation_task)
    generation_task.add_done_callback(_storyboard_generation_tasks.discard)

    async def event_generator():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/storyboard-generations", response_model=list[StoryboardGenerationTaskResponse])
async def list_storyboard_generations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StoryboardGenerationTask)
        .order_by(StoryboardGenerationTask.created_at.desc())
        .limit(20)
    )
    return [_serialize_storyboard_task(task) for task in result.scalars().all()]


@router.get("/storyboards", response_model=list[StoryboardHistoryItem])
async def list_storyboards(
    db: AsyncSession = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    storyboard_page = (
        select(
            Storyboard.id,
            Storyboard.title,
            Storyboard.brief,
            Storyboard.total_duration_sec,
            Storyboard.created_at,
        )
        .order_by(Storyboard.created_at.desc())
        .limit(limit)
        .offset(offset)
        .subquery()
    )
    shot_counts = (
        select(
            StoryboardShot.storyboard_id,
            func.count(StoryboardShot.id).label("shot_count"),
        )
        .where(StoryboardShot.storyboard_id.in_(select(storyboard_page.c.id)))
        .group_by(StoryboardShot.storyboard_id)
        .subquery()
    )
    result = await db.execute(
        select(
            storyboard_page.c.id,
            storyboard_page.c.title,
            storyboard_page.c.brief,
            storyboard_page.c.total_duration_sec,
            storyboard_page.c.created_at,
            func.coalesce(shot_counts.c.shot_count, 0).label("shot_count"),
        )
        .outerjoin(shot_counts, shot_counts.c.storyboard_id == storyboard_page.c.id)
        .order_by(storyboard_page.c.created_at.desc())
    )
    return [
        StoryboardHistoryItem(
            id=row.id,
            title=row.title,
            brief=row.brief[:200],
            total_duration_sec=row.total_duration_sec,
            shot_count=row.shot_count,
            created_at=row.created_at,
        )
        for row in result.all()
    ]


@router.get("/storyboards/{storyboard_id}", response_model=StoryboardDetailResponse)
async def get_storyboard(storyboard_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Storyboard)
        .where(Storyboard.id == storyboard_id)
        .options(selectinload(Storyboard.shots))
    )
    sb = result.scalar_one_or_none()
    if not sb:
        raise HTTPException(404, "Storyboard not found")
    return sb


@router.post("/storyboards/{storyboard_id}/shots/{shot_number}/image", response_model=StoryboardShotDetail)
async def retry_storyboard_shot_image(storyboard_id: str, shot_number: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StoryboardShot)
        .where(
            StoryboardShot.storyboard_id == storyboard_id,
            StoryboardShot.shot_number == shot_number,
        )
    )
    shot = result.scalar_one_or_none()
    if not shot:
        raise HTTPException(404, "Storyboard shot not found")
    prompt = shot.image_prompt or shot.description or ""
    return await _generate_and_store_storyboard_image(
        storyboard_id=storyboard_id,
        shot_number=shot_number,
        prompt=prompt,
    )


@router.delete("/storyboards/{storyboard_id}")
async def delete_storyboard(storyboard_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Storyboard).where(Storyboard.id == storyboard_id))
    sb = result.scalar_one_or_none()
    if not sb:
        raise HTTPException(404, "Storyboard not found")
    await db.delete(sb)
    await db.commit()
    return {"deleted": True}


@router.get("/settings", response_model=list[SystemSettingResponse])
async def list_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemSetting))
    settings = result.scalars().all()
    return [_serialize_setting(setting) for setting in settings]


@router.put("/settings/{key}", response_model=SystemSettingResponse)
async def update_setting(key: str, req: UpdateSettingRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    incoming_value = _validated_setting_value(key, req.value)
    if key in SECRET_SETTING_KEYS and not _is_masked_secret(incoming_value):
        from backend.services.secret_store import store_secret_value
        incoming_value = await asyncio.to_thread(store_secret_value, key, incoming_value)
    if not setting:
        setting = SystemSetting(key=key, value="" if key in SECRET_SETTING_KEYS and _is_masked_secret(incoming_value) else incoming_value)
        db.add(setting)
    else:
        if not (key in SECRET_SETTING_KEYS and _is_masked_secret(incoming_value)):
            setting.value = incoming_value
    await db.commit()
    await db.refresh(setting)
    return _serialize_setting(setting)


async def _connectivity_config(
    engine_name: str,
    request: Optional[ConnectivityTestRequest],
    default_model: str,
) -> tuple[str, str, str]:
    from backend.database import get_system_setting

    requested_key = request.api_key.strip() if request and request.api_key else ""
    api_key = requested_key or await get_system_setting(f"{engine_name}_api_key")
    requested_model = request.model if request and request.model is not None else None
    model = requested_model if requested_model is not None else await get_system_setting(f"{engine_name}_model", default_model)
    requested_url = request.base_url if request and request.base_url is not None else None
    base_url = requested_url if requested_url is not None else await get_system_setting(
        f"{engine_name}_base_url",
        "https://api.stepfun.com/v1",
    )

    if not api_key:
        raise HTTPException(400, "API 密钥未配置")
    return (
        api_key,
        _validated_connectivity_value(model, "模型标识"),
        _validated_connectivity_url(base_url),
    )


def _remote_error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("error", {}).get("message", "")
    except Exception:
        detail = response.text[:200]
    return detail or f"HTTP {response.status_code}"


@router.post("/settings/test-connectivity", response_model=ImageConnectivityResponse)
async def test_connectivity(request: Optional[ConnectivityTestRequest] = None):
    engine_name = request.engine if request else "analysis"
    if engine_name not in {"analysis", "storyboard"}:
        raise HTTPException(400, "文本连接测试仅支持分析或分镜引擎")

    api_key, model, base_url = await _connectivity_config(engine_name, request, "step-3.7-flash")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "say hi"}],
        "max_tokens": 5,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"连接失败：{_remote_error_detail(resp)}")
        return ImageConnectivityResponse(
            status="success",
            message=f"连接正常，可使用 {model}",
            engine=engine_name,
            checked_at=datetime.now(timezone.utc),
        )
    except httpx.ConnectError:
        raise HTTPException(503, "无法连接到 API 地址，请检查网络或 URL")
    except httpx.TimeoutException:
        raise HTTPException(504, "连接测试超时，请检查网络或接口地址")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"测试出错: {str(e)}")


@router.post("/settings/test-image-connectivity", response_model=ImageConnectivityResponse)
async def test_image_connectivity(request: Optional[ConnectivityTestRequest] = None):
    api_key, model, base_url = await _connectivity_config("image", request, "step-image-edit-2")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code == 200:
            model_seen = False
            try:
                data = resp.json()
                model_seen = any(item.get("id") == model for item in data.get("data", []))
            except Exception:
                model_seen = False
            suffix = "，模型可见" if model_seen else "，认证可用"
            return ImageConnectivityResponse(
                status="success",
                message=f"图像接口连接成功{suffix}",
                engine="image",
                checked_at=datetime.now(timezone.utc),
            )
        raise HTTPException(resp.status_code, f"图像接口连接失败：{_remote_error_detail(resp)}")
    except httpx.ConnectError:
        raise HTTPException(503, "无法连接到图像 API 地址，请检查网络或 URL")
    except httpx.TimeoutException:
        raise HTTPException(504, "图像连接测试超时，请检查网络或接口地址")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"图像测试出错: {str(e)}")
