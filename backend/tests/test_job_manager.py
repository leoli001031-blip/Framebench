import asyncio
import json
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models import Job, Shot
from backend.services.job_manager import JobManager


class JobManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_mark_overview_failed_preserves_job_status_and_broadcasts(self):
        async with self.session_factory() as db:
            db.add(Job(
                id="11111111-1111-1111-1111-111111111111",
                filename="video.mp4",
                video_path="/tmp/video.mp4",
                status="completed",
            ))
            await db.commit()

        manager = JobManager()
        events: list[tuple[str, dict]] = []

        async def capture(job_id: str, event: dict):
            events.append((job_id, event))

        with (
            patch("backend.services.job_manager.AsyncSessionLocal", self.session_factory),
            patch.object(manager, "_broadcast", side_effect=capture),
        ):
            await manager._mark_overview_failed(
                "11111111-1111-1111-1111-111111111111",
                "综述生成失败：连续 3 次未返回有效内容",
            )

        async with self.session_factory() as db:
            result = await db.execute(select(Job).where(Job.id == "11111111-1111-1111-1111-111111111111"))
            job = result.scalar_one()

        self.assertEqual(job.status, "completed")
        self.assertEqual(job.error_message, "综述生成失败：连续 3 次未返回有效内容")
        self.assertEqual(events[0][0], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(events[0][1]["event"], "overview_failed")
        self.assertEqual(events[0][1]["data"]["phase"], "overview")

    async def test_resume_restores_frame_features_and_optical_flow(self):
        job_id = "33333333-3333-3333-3333-333333333333"
        async with self.session_factory() as db:
            db.add(Job(
                id=job_id,
                filename="video.mp4",
                video_path="/tmp/video.mp4",
                status="failed",
            ))
            db.add(Shot(
                job_id=job_id,
                shot_number=1,
                start_time_sec=0,
                end_time_sec=1,
                keyframe_paths='["frame.jpg"]',
                status="failed",
            ))
            await db.commit()

        with TemporaryDirectory() as tmpdir:
            job_dir = os.path.join(tmpdir, job_id)
            frame_dir = os.path.join(job_dir, "frames", "shot_0001")
            os.makedirs(frame_dir)
            open(os.path.join(frame_dir, "frame_start.jpg"), "wb").close()
            with open(os.path.join(job_dir, "transcript.json"), "w", encoding="utf-8") as f:
                json.dump([], f)
            with open(os.path.join(job_dir, "audio_analysis.json"), "w", encoding="utf-8") as f:
                json.dump({}, f)
            with open(os.path.join(job_dir, "analysis_inputs.json"), "w", encoding="utf-8") as f:
                json.dump([{
                    "shot_number": 1,
                    "frame_features": {"mean_brightness": 0.4},
                    "frame_features_by_frame": {"start": {"mean_brightness": 0.4}},
                    "optical_flow": {"前半段": "固定"},
                }], f, ensure_ascii=False)

            with patch("backend.services.job_manager.AsyncSessionLocal", self.session_factory):
                resume_data = await JobManager()._load_resume_data(job_id, job_dir)

        self.assertIsNotNone(resume_data)
        _, shots_to_analyze, _, _ = resume_data
        self.assertEqual(shots_to_analyze[0]["frame_features_by_frame"]["start"]["mean_brightness"], 0.4)
        self.assertEqual(shots_to_analyze[0]["optical_flow"], {"前半段": "固定"})

    async def test_slow_sse_subscriber_keeps_bounded_recent_events(self):
        manager = JobManager()
        queue = manager.subscribe("job-1")

        for index in range(queue.maxsize + 1):
            await manager._broadcast("job-1", {"event": "status", "data": {"index": index}})

        self.assertEqual(queue.qsize(), queue.maxsize)
        self.assertEqual((await queue.get())["data"]["index"], 1)

    async def test_start_reports_when_job_is_already_running(self):
        manager = JobManager()
        blocker = asyncio.Event()

        async def blocked_run(_job_id: str):
            await blocker.wait()

        with patch.object(manager, "_run", side_effect=blocked_run):
            self.assertTrue(manager.start("job-1"))
            self.assertFalse(manager.start("job-1"))
            blocker.set()
            await manager._tasks["job-1"]

    async def test_preprocessing_cancel_sets_event_without_cancelling_task_wrapper(self):
        manager = JobManager()
        blocker = asyncio.Event()

        async def blocked_run(_job_id: str):
            await blocker.wait()

        with patch.object(manager, "_run", side_effect=blocked_run):
            self.assertTrue(manager.start("job-1"))
            task = manager._tasks["job-1"]
            manager._phases["job-1"] = "preprocessing"

            self.assertTrue(await manager.request_cancel("job-1"))
            await asyncio.sleep(0)

            self.assertTrue(manager._cancel_events["job-1"].is_set())
            self.assertFalse(task.done())
            blocker.set()
            await task

    async def test_analysis_cancel_also_cancels_async_task(self):
        manager = JobManager()
        blocker = asyncio.Event()

        async def blocked_run(_job_id: str):
            await blocker.wait()

        with patch.object(manager, "_run", side_effect=blocked_run):
            self.assertTrue(manager.start("job-1"))
            task = manager._tasks["job-1"]
            manager._phases["job-1"] = "analyzing"

            self.assertTrue(await manager.request_cancel("job-1"))
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(manager._cancel_events["job-1"].is_set())

    async def test_cancel_unknown_job_is_not_accepted(self):
        manager = JobManager()

        self.assertFalse(await manager.request_cancel("missing"))

    async def test_cancel_is_rejected_after_job_enters_finished_boundary(self):
        manager = JobManager()
        blocker = asyncio.Event()

        async def blocked_run(_job_id: str):
            await blocker.wait()

        with patch.object(manager, "_run", side_effect=blocked_run):
            self.assertTrue(manager.start("job-1"))
            manager._phases["job-1"] = "finished"

            self.assertFalse(await manager.request_cancel("job-1"))
            self.assertFalse(manager._cancel_events["job-1"].is_set())
            blocker.set()
            await manager._tasks["job-1"]

    async def test_preprocessing_cancel_waits_for_worker_then_persists_final_state(self):
        job_id = "88888888-8888-8888-8888-888888888888"
        async with self.session_factory() as db:
            db.add(Job(
                id=job_id,
                filename="video.mp4",
                video_path="/tmp/video.mp4",
                status="pending",
            ))
            await db.commit()

        started = asyncio.Event()
        worker_stopped = asyncio.Event()

        async def cancellable_run(_self, _job_id, _video_path, _queue, cancel_check=None):
            started.set()
            while not (cancel_check and cancel_check()):
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            worker_stopped.set()
            raise asyncio.CancelledError()

        manager = JobManager()
        with TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, job_id))
            with (
                patch("backend.services.job_manager.AsyncSessionLocal", self.session_factory),
                patch("backend.services.job_manager.JOBS_DIR", tmpdir),
                patch("backend.services.preprocess.Preprocessor.run", new=cancellable_run),
            ):
                self.assertTrue(manager.start(job_id))
                task = manager._tasks[job_id]
                await asyncio.wait_for(started.wait(), timeout=1)

                self.assertTrue(await manager.request_cancel(job_id))
                self.assertFalse(task.done())
                self.assertFalse(worker_stopped.is_set())
                await asyncio.wait_for(task, timeout=1)

        async with self.session_factory() as db:
            stored = await db.get(Job, job_id)

        self.assertTrue(worker_stopped.is_set())
        self.assertEqual(stored.status, "failed")
        self.assertEqual(stored.error_message, "Cancelled by user")
        self.assertNotIn(job_id, manager._tasks)
        self.assertNotIn(job_id, manager._cancel_events)


if __name__ == "__main__":
    unittest.main()
