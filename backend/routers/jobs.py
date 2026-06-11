import asyncio
import json
import os
import shutil
import time
import traceback
import uuid
from typing import Annotated, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Request, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from backend.database import get_db, AsyncSessionLocal
from backend.models import (
    Job, Shot, Dimension, TranscriptSegment, Storyboard, StoryboardShot,
    StoryboardGenerationTask, SystemSetting,
)
from backend.schemas import (
    JobListResponse, JobProgressResponse, JobResponse, JobDetailResponse,
    JobWithShotsResponse, ShotProgressResponse,
    JobShotsPageResponse,
    UploadResponse, StartResponse, DeleteResponse,
    UpdateJobRequest, GenerateStoryboardRequest, StoryboardResponse,
    CategoryListResponse, StoryboardHistoryItem, StoryboardDetailResponse,
    StoryboardGenerationTaskResponse, SystemSettingResponse, UpdateSettingRequest,
)
from backend.config import JOBS_DIR, MAX_VIDEO_SIZE_MB
from backend.services.job_manager import job_manager

router = APIRouter(prefix="/api")

SECRET_SETTING_KEYS = {"analysis_api_key", "storyboard_api_key", "moonshot_api_key"}
MASK_PREFIX = "••••••••"
_storyboard_generation_tasks: set[asyncio.Task] = set()


def _read_report(report_path: str) -> str:
    """Read report file (blocking I/O, intended for asyncio.to_thread)."""
    with open(report_path, "r") as f:
        return f.read()


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
    return f"{MASK_PREFIX}{value[-4:]}" if len(value) > 4 else MASK_PREFIX


def _is_masked_secret(value: str) -> bool:
    return value.startswith(MASK_PREFIX)


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
        target_duration_sec=task.target_duration_sec,
        status=task.status,
        progress=task.progress or 0.0,
        message=task.message,
        storyboard_id=task.storyboard_id,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


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

    job_manager.start(job_id)
    return StartResponse(job_id=job_id, status="preprocessing")


@router.get("/jobs", response_model=list[JobListResponse])
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
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
        )
        .order_by(Job.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [JobListResponse(**row._mapping) for row in result.all()]


@router.get("/jobs/{job_id}", response_model=JobWithShotsResponse)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    validate_job_id(job_id)
    result = await db.execute(
        select(Job)
        .where(Job.id == job_id)
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
        )
        .where(Job.id == job_id)
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
    job_exists = await db.scalar(select(func.count(Job.id)).where(Job.id == job_id))
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
        )
        .where(Job.id == job_id)
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
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    if not os.path.exists(job.video_path):
        raise HTTPException(404, "Video file not found")

    file_size = os.path.getsize(job.video_path)
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
            _iter_file_range(job.video_path, start, end),
            status_code=206,
            headers=headers,
        )

    headers["Content-Length"] = str(file_size)
    return StreamingResponse(
        _iter_file_range(job.video_path, 0, file_size - 1),
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
            job.status = "cancelling"
            job.error_message = "Cancellation requested"
            await db.commit()
            await job_manager.request_cancel(job_id)
            return DeleteResponse(deleted=False)

        await db.delete(job)
        await db.commit()

    # Remove files (best-effort; DB record already deleted)
    job_dir = os.path.join(JOBS_DIR, job_id)
    if os.path.exists(job_dir):
        try:
            shutil.rmtree(job_dir)
        except OSError:
            pass

    return DeleteResponse(deleted=True)


@router.get("/jobs/{job_id}/report")
async def get_report(job_id: str, format: str = "md"):
    validate_job_id(job_id)
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


@router.put("/jobs/{job_id}", response_model=JobResponse)
async def update_job(job_id: str, req: UpdateJobRequest):
    validate_job_id(job_id)
    from backend.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
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
        select(Job.category).where(Job.category != None).distinct().order_by(Job.category)
    )
    cats = [row[0] for row in result.all() if row[0]]
    return CategoryListResponse(categories=cats)


@router.post("/generate-storyboard")
async def generate_storyboard(req: GenerateStoryboardRequest):
    if not req.reference_job_ids:
        raise HTTPException(400, "At least one reference job is required")

    from backend.services.storyboard_generator import generate_storyboard as run_storyboard_generation

    queue: asyncio.Queue = asyncio.Queue()
    task_id = _safe_client_task_id(req.client_task_id)
    async with AsyncSessionLocal() as db:
        if await db.get(StoryboardGenerationTask, task_id):
            task_id = str(uuid.uuid4())
        task = StoryboardGenerationTask(
            id=task_id,
            brief=req.brief,
            reference_job_ids=json.dumps(req.reference_job_ids),
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
        try:
            print(
                f"Storyboard generation started: refs={len(req.reference_job_ids)}, "
                f"target_duration={req.target_duration_sec or 'unset'}"
            )
            await publish_progress("正在读取参考素材…", "collecting", 0.08, force=True)
            # Collect reference analyses
            references = []
            async with AsyncSessionLocal() as db:
                for jid in req.reference_job_ids:
                    result = await db.execute(
                        select(Job).where(Job.id == jid).options(selectinload(Job.shots))
                    )
                    job = result.scalar_one_or_none()
                    if not job:
                        message = f"Reference job {jid} not found"
                        await _update_storyboard_task(
                            task_id,
                            status="failed",
                            message=message,
                            error_message=message,
                        )
                        await queue.put({"event": "error", "data": {"task_id": task_id, "message": message}})
                        return

                    shot_data = []
                    all_techs = []
                    for shot in list(job.shots)[:50]:
                        if shot.analysis_text:
                            shot_data.append({
                                "shot_number": shot.shot_number,
                                "duration_sec": shot.end_time_sec - shot.start_time_sec,
                                "analysis_text": shot.analysis_text,
                                "techniques": json.loads(shot.techniques_json or "[]"),
                            })
                            all_techs.extend(json.loads(shot.techniques_json or "[]"))

                    references.append({
                        "filename": job.filename,
                        "category": job.category or "",
                        "overview_text": job.overview_text or "",
                        "shots": shot_data,
                        "all_techniques": all_techs,
                    })

            async def on_progress(msg: str):
                status, progress = _storyboard_progress_for_message(msg)
                await publish_progress(msg, status, progress)

            result = await run_storyboard_generation(
                req.brief, references, req.target_duration_sec,
                progress_callback=on_progress,
            )

            # Save to history
            await publish_progress("正在保存分镜脚本…", "saving", 0.94, force=True)
            sb_id = str(uuid.uuid4())
            async with AsyncSessionLocal() as db:
                sb = Storyboard(
                    id=sb_id,
                    title=result["title"],
                    brief=req.brief,
                    full_notes=result.get("full_notes", ""),
                    total_duration_sec=result.get("total_duration_sec", 0),
                    reference_job_ids=json.dumps(req.reference_job_ids),
                )
                db.add(sb)
                for s in result.get("shots", []):
                    db.add(StoryboardShot(
                        storyboard_id=sb_id,
                        shot_number=s["shot_number"],
                        duration_sec=s["duration_sec"],
                        description=s["description"],
                        camera_movement=s.get("camera_movement", ""),
                        bgm_note=s.get("bgm_note", ""),
                        reference_from=s.get("reference_from", ""),
                        image_prompt=s.get("image_prompt", ""),
                    ))
                await db.commit()

            result["id"] = sb_id
            await _update_storyboard_task(
                task_id,
                status="completed",
                progress=1.0,
                message="分镜脚本已保存",
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
    incoming_value = req.value
    if not setting:
        setting = SystemSetting(key=key, value="" if key in SECRET_SETTING_KEYS and _is_masked_secret(incoming_value) else incoming_value)
        db.add(setting)
    else:
        if not (key in SECRET_SETTING_KEYS and _is_masked_secret(incoming_value)):
            setting.value = incoming_value
    await db.commit()
    await db.refresh(setting)
    return _serialize_setting(setting)


@router.post("/settings/test-connectivity")
async def test_connectivity():
    from backend.database import get_system_setting
    import httpx
    
    api_key = await get_system_setting("analysis_api_key")
    model = await get_system_setting("analysis_model", "kimi-k2.6")
    base_url = await get_system_setting("analysis_base_url", "https://api.moonshot.cn/v1")
    
    if not api_key:
        raise HTTPException(400, "API 密钥未配置")
        
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "say hi"}],
        "max_tokens": 5
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
            
            if resp.status_code == 200:
                return {"status": "success", "message": "连接成功"}
            else:
                detail = ""
                try:
                    detail = resp.json().get("error", {}).get("message", "")
                except:
                    detail = resp.text[:100]
                raise HTTPException(resp.status_code, f"连接失败: {detail}")
    except httpx.ConnectError:
        raise HTTPException(503, "无法连接到 API 地址，请检查网络或 URL")
    except Exception as e:
        raise HTTPException(500, f"测试出错: {str(e)}")
