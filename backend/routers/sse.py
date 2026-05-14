import asyncio
import json
import uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from backend.database import AsyncSessionLocal
from backend.models import Job
from backend.services.job_manager import job_manager

router = APIRouter()


@router.get("/api/jobs/{job_id}/sse")
async def job_sse(job_id: str):
    # Validate job_id is a UUID to prevent path traversal
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(400, "Invalid job ID format")

    queue = job_manager.subscribe(job_id)

    async def get_job_status():
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Job).where(Job.id == job_id))
            return result.scalar_one_or_none()

    async def event_generator():
        try:
            job = await get_job_status()
            if job:
                yield f"event: status\ndata: {json.dumps({'phase': job.status, 'progress': job.progress, 'message': job.error_message or job.status}, ensure_ascii=False)}\n\n"
                if job.status in ("completed", "partial_completed", "failed"):
                    yield f"event: done\ndata: {json.dumps({'status': job.status})}\n\n"
                    return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive
                    yield ": keepalive\n\n"
                    # Check if job is done
                    job = await get_job_status()
                    if job and job.status in ("completed", "partial_completed", "failed"):
                        yield f"event: done\ndata: {json.dumps({'status': job.status})}\n\n"
                        break
        finally:
            # Clean up only this subscriber's queue on disconnect
            job_manager.unsubscribe(job_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
