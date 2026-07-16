import asyncio
import json
import os
import shutil
import threading
import traceback
import httpx
from backend.config import JOBS_DIR
from backend.database import AsyncSessionLocal
from sqlalchemy import delete, func, select
from backend.models import Job, Shot, TranscriptSegment, Dimension
from backend.services.model_retry import is_retryable_model_error, model_retry_delay
from backend.services.perf import perf_now, record_duration, record_metrics


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
        self._cancel_events: dict[str, threading.Event] = {}
        self._phases: dict[str, str] = {}

    def subscribe(self, job_id: str) -> asyncio.Queue:
        """Create a personal queue for one SSE client."""
        q = asyncio.Queue(maxsize=256)
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
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    dead.append(q)
        for q in dead:
            self._subscribers[job_id].discard(q)
        if not self._subscribers.get(job_id):
            self._subscribers.pop(job_id, None)

    async def _mark_overview_failed(self, job_id: str, message: str):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            if not job:
                return
            existing = job.error_message or ""
            job.error_message = f"{existing}; {message}"[:500] if existing else message[:500]
            await db.commit()

        await self._broadcast(job_id, {
            "event": "overview_failed",
            "data": {"phase": "overview", "error": message},
        })

    def start(self, job_id: str) -> bool:
        if job_id in self._tasks and not self._tasks[job_id].done():
            return False
        self._cancel_events[job_id] = threading.Event()
        self._phases[job_id] = "starting"
        self._tasks[job_id] = asyncio.create_task(self._run(job_id))
        return True

    async def request_cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        cancel_event = self._cancel_events.get(job_id)
        if task is None or task.done() or cancel_event is None:
            return False
        if self._phases.get(job_id) == "finished":
            return False

        cancel_event.set()
        if self._phases.get(job_id) in {"analyzing", "overview"}:
            task.cancel()
        await self._broadcast(job_id, {
            "event": "status",
            "data": {"phase": "cancelling", "message": "Cancelling job..."}
        })
        return True

    def is_running(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        return bool(task and not task.done())

    def is_cancel_requested(self, job_id: str) -> bool:
        cancel_event = self._cancel_events.get(job_id)
        return bool(cancel_event and cancel_event.is_set())

    async def _run(self, job_id: str):
        from backend.services.preprocess import Preprocessor
        from backend.services.analysis import AnalysisService

        job_dir = os.path.join(JOBS_DIR, job_id)
        video_path = os.path.join(job_dir, "original.mp4")
        job_started = perf_now()
        cancel_event = self._cancel_events.setdefault(job_id, threading.Event())
        cancel_check = cancel_event.is_set

        try:
            bq = _BroadcastQueue(self, job_id)
            resume_data = await self._load_resume_data(job_id, job_dir)
            self._raise_if_cancelled(cancel_check)

            if resume_data:
                self._phases[job_id] = "analyzing"
                shots, shots_to_analyze, transcript, audio_analysis = resume_data
                await asyncio.to_thread(self._clean_report_only, job_dir)
                self._raise_if_cancelled(cancel_check)
                record_metrics(job_dir, {
                    "resume_mode": "failed_or_pending_shots",
                    "resume_shots": len(shots_to_analyze),
                })
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(Job).where(Job.id == job_id))
                    job = result.scalar_one()
                    job.status = "analyzing"
                    job.error_message = None
                    job.progress = max(job.progress or 0.0, 0.3)
                    job.total_shots = len(shots)
                    job.duration_sec = shots[-1]["end_time_sec"] if shots else job.duration_sec
                    await db.commit()
            else:
                self._phases[job_id] = "preprocessing"
                phase_started = perf_now()
                await asyncio.to_thread(self._clean_generated_outputs, job_dir)
                self._raise_if_cancelled(cancel_check)
                record_duration(job_dir, "cleanup_sec", phase_started)

                # Phase 1: Preprocessing
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(Job).where(Job.id == job_id))
                    job = result.scalar_one()
                    job.status = "preprocessing"
                    job.progress = 0.0
                    job.total_shots = None
                    job.duration_sec = None
                    job.error_message = None
                    job.overview_text = None
                    # Clean up old shot/transcript data from previous run to avoid IntegrityError
                    shot_ids = (await db.execute(select(Shot.id).where(Shot.job_id == job_id))).scalars().all()
                    if shot_ids:
                        await db.execute(delete(Dimension).where(Dimension.shot_id.in_(shot_ids)))
                    await db.execute(delete(Shot).where(Shot.job_id == job_id))
                    await db.execute(delete(TranscriptSegment).where(TranscriptSegment.job_id == job_id))
                    await db.commit()

                preprocessor = Preprocessor()
                phase_started = perf_now()
                shots, transcript, audio_analysis = await preprocessor.run(
                    job_id, video_path, bq,
                    cancel_check=cancel_check,
                )
                self._raise_if_cancelled(cancel_check)
                shots_to_analyze = shots
                record_duration(job_dir, "preprocess_total_sec", phase_started)

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
                record_metrics(job_dir, {"final_status": "failed", "error": "未检测到镜头变化"})
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
                job.error_message = None
                await db.commit()

            if not resume_data:
                # Phase 2: AI Analysis (vision-based via configured API)
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(Job).where(Job.id == job_id))
                    job = result.scalar_one()
                    job.status = "analyzing"
                    job.error_message = None
                    await db.commit()

            if shots_to_analyze:
                self._phases[job_id] = "analyzing"
                self._raise_if_cancelled(cancel_check)
                analysis_service = AnalysisService()
                phase_started = perf_now()
                await analysis_service.run(
                    job_id, shots_to_analyze, transcript, audio_analysis, bq,
                    cancel_check=cancel_check,
                )
                self._raise_if_cancelled(cancel_check)
                record_duration(job_dir, "analysis_total_sec", phase_started)
            else:
                record_metrics(job_dir, {"analysis_total_sec": 0, "resume_shots": 0})

            # Re-read shot analyses and status counts in bounded queries for overview/final state.
            async with AsyncSessionLocal() as db:
                shot_result = await db.execute(
                    select(Shot).where(Shot.job_id == job_id).order_by(Shot.shot_number)
                )
                shots_by_number = {shot.shot_number: shot for shot in shot_result.scalars().all()}
                for s in shots:
                    db_shot = shots_by_number.get(s["shot_number"])
                    if db_shot:
                        s["analysis_text"] = db_shot.analysis_text or ""
                        try:
                            s["techniques"] = json.loads(db_shot.techniques_json or "[]")
                        except (json.JSONDecodeError, TypeError):
                            s["techniques"] = []

                counts_result = await db.execute(
                    select(Shot.status, func.count(Shot.id))
                    .where(Shot.job_id == job_id)
                    .group_by(Shot.status)
                )
                status_counts = {status: count for status, count in counts_result.all()}
                completed_count = int(status_counts.get("completed", 0))
                failed_count = int(status_counts.get("failed", 0))

                job = await db.get(Job, job_id)
                if not job:
                    raise RuntimeError(f"Job not found: {job_id}")
                pending_count = max(0, len(shots) - completed_count - failed_count)
                record_metrics(job_dir, {
                    "completed_shots": completed_count,
                    "failed_shots": failed_count,
                    "pending_shots": pending_count,
                })
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
                self._phases[job_id] = "overview"
                self._raise_if_cancelled(cancel_check)
                await self._broadcast(job_id, {
                    "event": "status",
                    "data": {"phase": "analyzing", "step": "overview", "message": "Generating overview..."}
                })
                phase_started = perf_now()
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
                    overview_error: Exception | None = None
                    for attempt in range(3):
                        try:
                            overview = await generate_overview(
                                shot_data,
                                audio_analysis,
                                transcript,
                                job_id=job_id,
                                attempt=attempt + 1,
                            )
                            overview_error = None
                        except Exception as exc:
                            overview_error = exc
                        if overview:
                            break
                        if overview_error is None:
                            overview_error = RuntimeError("模型未返回综述内容")
                        if attempt >= 2 or not is_retryable_model_error(overview_error):
                            break
                        await asyncio.sleep(model_retry_delay(overview_error, attempt))
                    if overview:
                        async with AsyncSessionLocal() as db:
                            result = await db.execute(select(Job).where(Job.id == job_id))
                            job = result.scalar_one()
                            job.overview_text = overview
                            await db.commit()
                    else:
                        detail = str(overview_error)[:300] if overview_error else "连续 3 次未返回有效内容"
                        await self._mark_overview_failed(job_id, f"综述生成失败：{detail}")
                except Exception as e:
                    await self._mark_overview_failed(job_id, f"综述生成失败：{e}")
                finally:
                    record_duration(job_dir, "overview_sec", phase_started)

                await self._broadcast(job_id, {
                    "event": "complete",
                    "data": {"job_id": job_id, "total_shots": completed_count, "message": f"Analysis complete: {completed_count}/{len(shots)} shots"}
                })
                record_metrics(job_dir, {"final_status": job.status})
            else:
                await self._broadcast(job_id, {
                    "event": "job_error",
                    "data": {"phase": "failed", "error": "All shots failed analysis"}
                })
                record_metrics(job_dir, {"final_status": "failed", "error": "All shots failed analysis"})

            self._raise_if_cancelled(cancel_check)
            self._phases[job_id] = "finished"

        except asyncio.CancelledError:
            self._phases[job_id] = "cancelling"
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
            record_metrics(job_dir, {"final_status": "failed", "error": "Cancelled by user"})

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
            record_metrics(job_dir, {"final_status": "failed", "error": str(e)[:500]})
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
            record_metrics(job_dir, {"final_status": "failed", "error": f"{type(e).__name__}: {str(e)[:500]}"})
            traceback.print_exc()

        finally:
            record_duration(job_dir, "job_total_sec", job_started)
            # Clean up to prevent memory leak
            self._tasks.pop(job_id, None)
            self._subscribers.pop(job_id, None)
            self._phases.pop(job_id, None)
            if self._cancel_events.get(job_id) is cancel_event:
                self._cancel_events.pop(job_id, None)

    @staticmethod
    def _raise_if_cancelled(cancel_check) -> None:
        if cancel_check():
            raise asyncio.CancelledError()

    def _clean_generated_outputs(self, job_dir: str):
        """Remove stale generated artifacts before retrying an existing job."""
        shutil.rmtree(os.path.join(job_dir, "frames"), ignore_errors=True)
        for name in (
            "shots.json",
            "analysis_inputs.json",
            "audio_analysis.json",
            "audio.wav",
            "transcript.json",
            "report.md",
            "performance.json",
        ):
            path = os.path.join(job_dir, name)
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def _clean_report_only(self, job_dir: str):
        try:
            os.remove(os.path.join(job_dir, "report.md"))
        except FileNotFoundError:
            pass

    async def _load_resume_data(self, job_id: str, job_dir: str):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            if not job or job.status not in ("failed", "partial_completed"):
                return None

            result = await db.execute(
                select(Shot)
                .where(Shot.job_id == job_id)
                .order_by(Shot.shot_number)
            )
            shot_rows = result.scalars().all()

        if not shot_rows:
            return None

        transcript_path = os.path.join(job_dir, "transcript.json")
        audio_analysis_path = os.path.join(job_dir, "audio_analysis.json")
        if not os.path.exists(transcript_path) or not os.path.exists(audio_analysis_path):
            return None

        analysis_inputs = self._read_json_file(os.path.join(job_dir, "analysis_inputs.json"), [])
        enriched_by_number = {
            int(item["shot_number"]): item
            for item in analysis_inputs
            if isinstance(item, dict) and "shot_number" in item
        }
        all_shots = [
            self._shot_to_run_dict(shot, enriched_by_number.get(shot.shot_number))
            for shot in shot_rows
        ]
        shots_to_analyze = [
            self._shot_to_run_dict(shot, enriched_by_number.get(shot.shot_number))
            for shot in shot_rows
            if shot.status != "completed"
        ]

        if any(not self._has_analysis_frame(job_dir, shot["shot_number"]) for shot in shots_to_analyze):
            return None

        transcript = self._read_json_file(transcript_path, [])
        audio_analysis = self._read_json_file(audio_analysis_path, {})
        return all_shots, shots_to_analyze, transcript, audio_analysis

    def _shot_to_run_dict(self, shot: Shot, enriched: dict | None = None) -> dict:
        data = {
            "shot_number": shot.shot_number,
            "start_time_sec": shot.start_time_sec,
            "end_time_sec": shot.end_time_sec,
            "duration_sec": shot.end_time_sec - shot.start_time_sec,
            "keyframe_paths": shot.keyframe_paths,
        }
        if isinstance(enriched, dict):
            for key in ("frame_features", "frame_features_by_frame", "optical_flow"):
                if key in enriched:
                    data[key] = enriched[key]
        return data

    def _has_analysis_frame(self, job_dir: str, shot_number: int) -> bool:
        return os.path.exists(
            os.path.join(job_dir, "frames", f"shot_{shot_number:04d}", "frame_start.jpg")
        )

    def _read_json_file(self, path: str, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default


job_manager = JobManager()
