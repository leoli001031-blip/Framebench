import asyncio
import json
import os
import traceback
import httpx
from backend.config import JOBS_DIR
from backend.database import AsyncSessionLocal
from sqlalchemy import select
from backend.models import Job, Shot, TranscriptSegment, Dimension
from sqlalchemy import delete


class _BroadcastQueue:
    """Forwards put() to all subscriber queues via the manager's broadcast."""
    def __init__(self, manager: 'JobManager', job_id: str):
        self._manager = manager
        self._job_id = job_id

    async def put(self, event):
        await self._manager._broadcast(self._job_id, event)


class JobManager:
    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_requested: set[str] = set()

    def subscribe(self, job_id: str) -> asyncio.Queue:
        """Create a personal queue for one SSE client."""
        q = asyncio.Queue()
        if job_id not in self._subscribers:
            self._subscribers[job_id] = set()
        self._subscribers[job_id].add(q)
        return q

    def unsubscribe(self, job_id: str, queue: asyncio.Queue):
        """Remove one subscriber's queue. Clean up the job entry if empty."""
        if job_id in self._subscribers:
            self._subscribers[job_id].discard(queue)
            if not self._subscribers[job_id]:
                del self._subscribers[job_id]

    async def _broadcast(self, job_id: str, event: dict):
        """Send an event to every subscriber for the given job."""
        if job_id not in self._subscribers:
            return
        dead: list[asyncio.Queue] = []
        for q in self._subscribers[job_id]:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers[job_id].discard(q)
        if not self._subscribers.get(job_id):
            self._subscribers.pop(job_id, None)

    def start(self, job_id: str):
        if job_id in self._tasks and not self._tasks[job_id].done():
            return
        self._cancel_requested.discard(job_id)
        self._tasks[job_id] = asyncio.create_task(self._run(job_id))

    async def request_cancel(self, job_id: str):
        self._cancel_requested.add(job_id)
        await self._broadcast(job_id, {
            "event": "status",
            "data": {"phase": "cancelling", "message": "Cancelling job..."}
        })

    def is_cancel_requested(self, job_id: str) -> bool:
        return job_id in self._cancel_requested

    async def _run(self, job_id: str):
        from backend.services.preprocess import Preprocessor
        from backend.services.analysis import AnalysisService

        job_dir = os.path.join(JOBS_DIR, job_id)
        video_path = os.path.join(job_dir, "original.mp4")

        try:
            # Phase 1: Preprocessing
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one()
                job.status = "preprocessing"
                # Clean up old shot/transcript data from previous run to avoid IntegrityError
                shot_ids = (await db.execute(select(Shot.id).where(Shot.job_id == job_id))).scalars().all()
                if shot_ids:
                    await db.execute(delete(Dimension).where(Dimension.shot_id.in_(shot_ids)))
                await db.execute(delete(Shot).where(Shot.job_id == job_id))
                await db.execute(delete(TranscriptSegment).where(TranscriptSegment.job_id == job_id))
                await db.commit()

            bq = _BroadcastQueue(self, job_id)
            preprocessor = Preprocessor()
            shots, transcript, audio_analysis = await preprocessor.run(
                job_id, video_path, bq,
                cancel_check=lambda: self.is_cancel_requested(job_id),
            )

            # Fail early if no shots were detected (prevents IndexError downstream)
            if not shots:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(Job).where(Job.id == job_id))
                    job = result.scalar_one()
                    job.status = "failed"
                    job.error_message = "未检测到镜头变化"
                    await db.commit()
                await self._broadcast(job_id, {
                    "event": "job_error",
                    "data": {"phase": "preprocessing", "error": "未检测到镜头变化"}
                })
                return

            # Compute video duration from the last shot's end time
            video_duration = shots[-1]["end_time_sec"]

            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one()
                job.status = "preprocessing_done"
                job.total_shots = len(shots)
                job.duration_sec = video_duration
                job.progress = 0.3
                await db.commit()

            # Phase 2: AI Analysis (vision-based via Moonshot API)
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one()
                job.status = "analyzing"
                await db.commit()

            analysis_service = AnalysisService()
            await analysis_service.run(
                job_id, shots, transcript, audio_analysis, bq,
                cancel_check=lambda: self.is_cancel_requested(job_id),
            )

            # Re-read shot analyses from DB to populate in-memory shots for overview
            async with AsyncSessionLocal() as db:
                for s in shots:
                    result = await db.execute(
                        select(Shot).where(
                            Shot.job_id == job_id,
                            Shot.shot_number == s["shot_number"],
                        )
                    )
                    db_shot = result.scalar_one_or_none()
                    if db_shot:
                        s["analysis_text"] = db_shot.analysis_text or ""
                        try:
                            s["techniques"] = json.loads(db_shot.techniques_json or "[]")
                        except (json.JSONDecodeError, TypeError):
                            s["techniques"] = []

            # Check how many shots were actually analyzed
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Shot).where(Shot.job_id == job_id, Shot.status == "completed")
                )
                completed_count = len(result.scalars().all())
                result = await db.execute(
                    select(Shot).where(Shot.job_id == job_id, Shot.status == "failed")
                )
                failed_count = len(result.scalars().all())

                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one()
                pending_count = max(0, len(shots) - completed_count - failed_count)
                if completed_count == 0 and len(shots) > 0:
                    job.status = "failed"
                    job.error_message = "All shots failed analysis"
                elif failed_count > 0 or completed_count < len(shots):
                    job.status = "partial_completed"
                    job.error_message = f"{failed_count} shots failed, {pending_count} shots missing"
                else:
                    job.status = "completed"
                    job.error_message = None
                job.progress = 1.0
                await db.commit()

            if job.status in ("completed", "partial_completed"):
                # Generate director's overview
                await self._broadcast(job_id, {
                    "event": "status",
                    "data": {"phase": "analyzing", "step": "overview", "message": "Generating overview..."}
                })
                try:
                    from backend.services.overview_generator import generate_overview
                    shot_data = [
                        {
                            "shot_number": s["shot_number"],
                            "start_time_sec": s["start_time_sec"],
                            "end_time_sec": s["end_time_sec"],
                            "analysis_text": s.get("analysis_text", ""),
                            "techniques": s.get("techniques", []),
                        }
                        for s in shots
                    ]
                    overview = ""
                    for attempt in range(3):
                        overview = await generate_overview(shot_data, audio_analysis, transcript)
                        if overview:
                            break
                        await asyncio.sleep(2)
                    if overview:
                        async with AsyncSessionLocal() as db:
                            result = await db.execute(select(Job).where(Job.id == job_id))
                            job = result.scalar_one()
                            job.overview_text = overview
                            await db.commit()
                    else:
                        print(f"Overview generation failed after 3 attempts")
                except Exception as e:
                    print(f"Overview generation failed: {e}")

                await self._broadcast(job_id, {
                    "event": "complete",
                    "data": {"job_id": job_id, "total_shots": completed_count, "message": f"Analysis complete: {completed_count}/{len(shots)} shots"}
                })
            else:
                await self._broadcast(job_id, {
                    "event": "job_error",
                    "data": {"phase": "failed", "error": "All shots failed analysis"}
                })

        except asyncio.CancelledError:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.error_message = "Cancelled by user"
                    await db.commit()
            await self._broadcast(job_id, {
                "event": "job_error",
                "data": {"phase": "cancelled", "error": "Cancelled by user"}
            })

        except (httpx.HTTPError, RuntimeError, OSError, ValueError) as e:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.error_message = str(e)[:500]
                    await db.commit()

            await self._broadcast(job_id, {
                "event": "job_error",
                "data": {"phase": "failed", "error": str(e)}
            })
            traceback.print_exc()

        except Exception as e:
            # Broad fallback for third-party library exceptions
            # (librosa, cv2, numpy, whisper, etc.) that would otherwise leave
            # the job stuck in a running state.
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.error_message = f"{type(e).__name__}: {str(e)[:500]}"
                    await db.commit()

            await self._broadcast(job_id, {
                "event": "job_error",
                "data": {"phase": "failed", "error": str(e)}
            })
            traceback.print_exc()

        finally:
            # Clean up to prevent memory leak
            self._tasks.pop(job_id, None)
            self._subscribers.pop(job_id, None)
            self._cancel_requested.discard(job_id)


job_manager = JobManager()
