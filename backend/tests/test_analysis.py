import asyncio
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models import Job, Shot
from backend.services.analysis import AnalysisService, _format_frame_features
from backend.services.api_runner import AnalysisApiConfig


class AnalysisServiceTests(unittest.TestCase):
    def test_save_parse_failure_writes_raw_model_output(self):
        service = AnalysisService()
        raw_output = "模型返回了非 JSON 文本"

        with TemporaryDirectory() as tmpdir:
            with patch("backend.services.analysis.JOBS_DIR", tmpdir):
                service._save_parse_failure("job-1", 3, raw_output)
                path = os.path.join(tmpdir, "job-1", "parse_failures", "shot_0003.txt")

                self.assertTrue(os.path.exists(path))
                with open(path, encoding="utf-8") as f:
                    self.assertEqual(f.read(), raw_output)

    def test_format_frame_features_includes_start_mid_end(self):
        text = _format_frame_features(
            {
                "start": {"mean_brightness": 0.1},
                "mid": {"mean_brightness": 0.5},
                "end": {"mean_brightness": 0.9},
            },
            {},
        )

        self.assertIn("起始[", text)
        self.assertIn("中段[", text)
        self.assertIn("结尾[", text)


class AnalysisEmptyResultTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_empty_model_object_marks_shot_failed_instead_of_pending(self):
        job_id = "11111111-1111-1111-1111-111111111111"
        async with self.session_factory() as db:
            db.add(Job(id=job_id, filename="video.mp4", video_path="/tmp/video.mp4", status="analyzing"))
            db.add(Shot(
                job_id=job_id,
                shot_number=1,
                start_time_sec=0,
                end_time_sec=1,
                keyframe_paths="[]",
                status="pending",
            ))
            await db.commit()

        with (
            patch("backend.services.analysis.AsyncSessionLocal", self.session_factory),
            patch(
                "backend.services.analysis.get_analysis_api_config",
                new=AsyncMock(return_value=AnalysisApiConfig("test-model", "https://example.test/v1", "test-key")),
            ),
            patch("backend.services.analysis.analyze_one_shot", new=AsyncMock(return_value={})),
            patch("backend.services.analysis._load_keyframe_images", return_value=[]),
        ):
            await AnalysisService().run(
                job_id,
                [{"shot_number": 1, "start_time_sec": 0, "end_time_sec": 1}],
                [],
                {},
                asyncio.Queue(),
            )

        async with self.session_factory() as db:
            shot = (await db.execute(select(Shot).where(Shot.job_id == job_id))).scalar_one()

        self.assertEqual(shot.status, "failed")
        self.assertIn("未返回镜头分析结果", shot.overall_notes)

    async def test_cancelled_shot_propagates_cancellation(self):
        job_id = "22222222-2222-2222-2222-222222222222"
        async with self.session_factory() as db:
            db.add(Job(id=job_id, filename="video.mp4", video_path="/tmp/video.mp4", status="analyzing"))
            db.add(Shot(
                job_id=job_id,
                shot_number=1,
                start_time_sec=0,
                end_time_sec=1,
                keyframe_paths="[]",
                status="pending",
            ))
            await db.commit()

        with (
            patch("backend.services.analysis.AsyncSessionLocal", self.session_factory),
            patch(
                "backend.services.analysis.get_analysis_api_config",
                new=AsyncMock(return_value=AnalysisApiConfig("test-model", "https://example.test/v1", "test-key")),
            ),
            patch(
                "backend.services.analysis.analyze_one_shot",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ),
            patch("backend.services.analysis._load_keyframe_images", return_value=[]),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await AnalysisService().run(
                    job_id,
                    [{"shot_number": 1, "start_time_sec": 0, "end_time_sec": 1}],
                    [],
                    {},
                    asyncio.Queue(),
                )


if __name__ == "__main__":
    unittest.main()
