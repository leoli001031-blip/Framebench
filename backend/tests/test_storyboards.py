import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models import Storyboard, StoryboardShot
from backend.routers.jobs import list_storyboards


class StoryboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_storyboards_counts_shots(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            storyboard = Storyboard(
                id="storyboard-1",
                title="测试分镜",
                brief="测试需求",
                total_duration_sec=8,
                reference_job_ids="[]",
            )
            db.add(storyboard)
            db.add_all([
                StoryboardShot(
                    storyboard_id=storyboard.id,
                    shot_number=1,
                    duration_sec=4,
                    description="第一镜",
                ),
                StoryboardShot(
                    storyboard_id=storyboard.id,
                    shot_number=2,
                    duration_sec=4,
                    description="第二镜",
                ),
            ])
            await db.commit()

            rows = await list_storyboards(db)

        await engine.dispose()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].shot_count, 2)

    async def test_list_storyboards_respects_limit_after_counting_current_page(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            now = datetime.now(timezone.utc)
            for idx in range(3):
                storyboard = Storyboard(
                    id=f"storyboard-{idx}",
                    title=f"测试分镜 {idx}",
                    brief="测试需求",
                    total_duration_sec=8,
                    reference_job_ids="[]",
                    created_at=now + timedelta(seconds=idx),
                )
                db.add(storyboard)
                db.add(StoryboardShot(
                    storyboard_id=storyboard.id,
                    shot_number=1,
                    duration_sec=4,
                    description="第一镜",
                ))
            await db.commit()

            rows = await list_storyboards(db, limit=2)

        await engine.dispose()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.shot_count for row in rows], [1, 1])


if __name__ == "__main__":
    unittest.main()
