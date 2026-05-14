import asyncio
import contextlib
import json
import os
import shutil
import uuid
from typing import Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from backend.database import get_db
from backend.models import Job, Shot, Dimension, TranscriptSegment, Storyboard, StoryboardShot, SystemSetting
from backend.schemas import (
    JobResponse, JobDetailResponse, JobWithShotsResponse,
    UploadResponse, StartResponse, DeleteResponse,
    UpdateJobRequest, GenerateStoryboardRequest, StoryboardResponse,
    CategoryListResponse, StoryboardHistoryItem, StoryboardDetailResponse,
    SystemSettingResponse, UpdateSettingRequest,
)
from backend.config import JOBS_DIR, MAX_VIDEO_SIZE_MB
from backend.services.job_manager import job_manager

router = APIRouter(prefix="/api")

SECRET_SETTING_KEYS = {"analysis_api_key", "storyboard_api_key", "moonshot_api_key"}
MASK_PREFIX = "••••••••"


def _read_report(report_path: str) -> str:
    """Read report file (blocking I/O, intended for asyncio.to_thread)."""
    with open(report_path, "r") as f:
        return f.read()


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


@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).order_by(Job.created_at.desc()))
    return result.scalars().all()


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

    from backend.database import AsyncSessionLocal
    from backend.services.storyboard_generator import generate_storyboard

    queue: asyncio.Queue = asyncio.Queue()

    async def collect_and_generate():
        """Collect references from DB and run AI generation, pushing progress to queue."""
        try:
            # Collect reference analyses
            references = []
            async with AsyncSessionLocal() as db:
                for jid in req.reference_job_ids:
                    result = await db.execute(
                        select(Job).where(Job.id == jid).options(selectinload(Job.shots))
                    )
                    job = result.scalar_one_or_none()
                    if not job:
                        await queue.put({"event": "error", "data": {"message": f"Reference job {jid} not found"}})
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
                await queue.put({"event": "progress", "data": {"message": msg}})

            result = await generate_storyboard(
                req.brief, references, req.target_duration_sec,
                progress_callback=on_progress,
            )

            # Save to history
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
            await queue.put({"event": "complete", "data": {"result": result}})
        except RuntimeError as e:
            await queue.put({"event": "error", "data": {"message": f"AI generation failed: {str(e)[:300]}"}})
        except Exception as e:
            await queue.put({"event": "error", "data": {"message": str(e)[:300]}})
        finally:
            await queue.put(None)  # Sentinel to close the stream

    # Start collection + generation in background
    generation_task = asyncio.create_task(collect_and_generate())

    async def event_generator():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        finally:
            if not generation_task.done():
                generation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await generation_task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/storyboards", response_model=list[StoryboardHistoryItem])
async def list_storyboards(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Storyboard)
        .options(selectinload(Storyboard.shots))
        .order_by(Storyboard.created_at.desc())
    )
    rows = result.scalars().all()
    return [
        StoryboardHistoryItem(
            id=r.id,
            title=r.title,
            brief=r.brief[:200],
            total_duration_sec=r.total_duration_sec,
            shot_count=len(r.shots),
            created_at=r.created_at,
        )
        for r in rows
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
    
    # Seed if empty or incomplete
    existing_keys = {s.key for s in settings}
    target_keys = {
        "analysis_api_key", "analysis_model", "analysis_base_url",
        "storyboard_api_key", "storyboard_model", "storyboard_base_url"
    }
    
    if not target_keys.issubset(existing_keys):
        from backend.config import MOONSHOT_API_KEY, MOONSHOT_MODEL, MOONSHOT_BASE_URL
        defaults = [
            SystemSetting(key="analysis_api_key", value=MOONSHOT_API_KEY, description="分析引擎 API 密钥"),
            SystemSetting(key="analysis_model", value=MOONSHOT_MODEL, description="分析引擎模型名称"),
            SystemSetting(key="analysis_base_url", value=MOONSHOT_BASE_URL, description="分析引擎接口地址"),
            SystemSetting(key="storyboard_api_key", value=MOONSHOT_API_KEY, description="分镜引擎 API 密钥"),
            SystemSetting(key="storyboard_model", value=MOONSHOT_MODEL, description="分镜引擎模型名称"),
            SystemSetting(key="storyboard_base_url", value=MOONSHOT_BASE_URL, description="分镜引擎接口地址"),
        ]
        for d in defaults:
            if d.key not in existing_keys:
                db.add(d)
        await db.commit()
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
