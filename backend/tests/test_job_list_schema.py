import unittest
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models import Dimension, Job, Shot
from backend.routers.jobs import get_job_progress, get_job_shots, get_job_summary, list_jobs
from backend.schemas import JobListResponse


class JobListSchemaTests(unittest.TestCase):
    def test_job_list_response_excludes_overview_text(self):
        now = datetime.now(timezone.utc)
        job = Job(
            id="job-1",
            filename="demo.mp4",
            video_path="/tmp/demo.mp4",
            status="completed",
            progress=1.0,
            overview_text="很长的分析综述",
            created_at=now,
            updated_at=now,
        )

        payload = JobListResponse.model_validate(job).model_dump()

        self.assertNotIn("overview_text", payload)
        self.assertEqual(payload["filename"], "demo.mp4")


class JobListEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_jobs_returns_lightweight_rows(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            db.add(Job(
                id="job-1",
                filename="demo.mp4",
                video_path="/tmp/demo.mp4",
                status="completed",
                progress=1.0,
                overview_text="很长的分析综述",
                created_at=now,
                updated_at=now,
            ))
            await db.commit()

            rows = await list_jobs(db)

        await engine.dispose()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].filename, "demo.mp4")
        self.assertNotIn("overview_text", rows[0].model_dump())

    async def test_list_jobs_respects_limit(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            for idx in range(3):
                db.add(Job(
                    id=f"job-{idx}",
                    filename=f"demo-{idx}.mp4",
                    video_path=f"/tmp/demo-{idx}.mp4",
                    status="completed",
                    progress=1.0,
                    created_at=now,
                    updated_at=now,
                ))
            await db.commit()

            rows = await list_jobs(db, limit=2)

        await engine.dispose()
        self.assertEqual(len(rows), 2)

    async def test_job_progress_returns_lightweight_shots(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            job_id = "11111111-1111-4111-8111-111111111111"
            db.add(Job(
                id=job_id,
                filename="demo.mp4",
                video_path="/tmp/demo.mp4",
                status="completed",
                progress=1.0,
                total_shots=1,
                overview_text="很长的分析综述",
                created_at=now,
                updated_at=now,
            ))
            db.add(Shot(
                job_id=job_id,
                shot_number=1,
                start_time_sec=0,
                end_time_sec=3,
                keyframe_paths="[]",
                status="completed",
                analysis_text="完成",
            ))
            await db.commit()

            result = await get_job_progress(job_id, db)

        await engine.dispose()
        payload = result.model_dump()
        self.assertNotIn("overview_text", payload)
        self.assertEqual(payload["shots"][0]["analysis_text"], "完成")
        self.assertNotIn("dimensions", payload["shots"][0])

    async def test_job_progress_defaults_to_bounded_shots_with_full_counts(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            job_id = "22222222-2222-4222-8222-222222222222"
            db.add(Job(
                id=job_id,
                filename="large.mp4",
                video_path="/tmp/large.mp4",
                status="analyzing",
                progress=0.5,
                total_shots=25,
                created_at=now,
                updated_at=now,
            ))
            for idx in range(25):
                db.add(Shot(
                    job_id=job_id,
                    shot_number=idx + 1,
                    start_time_sec=idx,
                    end_time_sec=idx + 1,
                    keyframe_paths="[]",
                    status="completed" if idx < 7 else "pending",
                ))
            await db.commit()

            result = await get_job_progress(job_id, db)

        await engine.dispose()
        payload = result.model_dump()
        self.assertEqual(payload["shots_total"], 25)
        self.assertEqual(payload["completed_shots"], 7)
        self.assertEqual(payload["pending_shots"], 18)
        self.assertLessEqual(len(payload["shots"]), 20)
        self.assertTrue(payload["shots_truncated"])

    async def test_job_progress_explicit_shot_pagination(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            job_id = "33333333-3333-4333-8333-333333333333"
            db.add(Job(
                id=job_id,
                filename="large.mp4",
                video_path="/tmp/large.mp4",
                status="completed",
                progress=1.0,
                total_shots=5,
                created_at=now,
                updated_at=now,
            ))
            for idx in range(5):
                db.add(Shot(
                    job_id=job_id,
                    shot_number=idx + 1,
                    start_time_sec=idx,
                    end_time_sec=idx + 1,
                    keyframe_paths="[]",
                    status="completed",
                ))
            await db.commit()

            result = await get_job_progress(job_id, db, include_shots=True, shot_limit=2, shot_offset=2)

        await engine.dispose()
        self.assertEqual([shot.shot_number for shot in result.shots], [3, 4])
        self.assertEqual(result.shot_offset, 2)
        self.assertEqual(result.shots_returned, 2)
        self.assertTrue(result.shots_truncated)

    async def test_job_summary_and_shots_are_split(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            job_id = "44444444-4444-4444-8444-444444444444"
            db.add(Job(
                id=job_id,
                filename="report.mp4",
                video_path="/tmp/report.mp4",
                status="completed",
                progress=1.0,
                total_shots=3,
                overview_text="全片综述",
                created_at=now,
                updated_at=now,
            ))
            await db.flush()
            shot_ids = []
            for idx in range(3):
                shot = Shot(
                    job_id=job_id,
                    shot_number=idx + 1,
                    start_time_sec=idx,
                    end_time_sec=idx + 1,
                    keyframe_paths="[]",
                    status="completed",
                    analysis_text=f"分析 {idx + 1}",
                )
                db.add(shot)
                await db.flush()
                shot_ids.append(shot.id)
            db.add(Dimension(
                shot_id=shot_ids[1],
                dimension_name="镜头",
                label="稳定",
            ))
            await db.commit()

            summary = await get_job_summary(job_id, db)
            page = await get_job_shots(job_id, db, limit=1, offset=1)

        await engine.dispose()
        self.assertEqual(summary.overview_text, "全片综述")
        self.assertFalse(hasattr(summary, "shots"))
        self.assertEqual(page.shots_total, 3)
        self.assertEqual([shot.shot_number for shot in page.shots], [2])
        self.assertEqual(page.shots[0].dimensions[0].dimension_name, "镜头")
        self.assertTrue(page.shots_truncated)


if __name__ == "__main__":
    unittest.main()
