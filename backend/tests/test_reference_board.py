import unittest
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models import Dimension, Job, Shot
from backend.routers.jobs import (
    _collect_storyboard_references,
    add_reference_board_shot,
    list_reference_board,
    remove_reference_board_shot,
)


class ReferenceBoardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with self.session_factory() as db:
            first_job = Job(
                id="11111111-1111-1111-1111-111111111111",
                filename="first.mp4",
                video_path="/tmp/first.mp4",
                status="completed",
                category="广告",
            )
            second_job = Job(
                id="22222222-2222-2222-2222-222222222222",
                filename="second.mp4",
                video_path="/tmp/second.mp4",
                status="completed",
                category="剧情",
            )
            db.add_all([first_job, second_job])
            await db.flush()
            shots = [
                Shot(
                    job_id=first_job.id,
                    shot_number=1,
                    start_time_sec=0,
                    end_time_sec=1,
                    keyframe_paths='["one.jpg"]',
                    status="completed",
                    analysis_text="第一镜分析",
                    techniques_json='["固定构图"]',
                ),
                Shot(
                    job_id=first_job.id,
                    shot_number=2,
                    start_time_sec=1,
                    end_time_sec=3,
                    keyframe_paths='["two.jpg"]',
                    status="completed",
                    analysis_text="第二镜分析",
                    techniques_json='["横移"]',
                ),
                Shot(
                    job_id=second_job.id,
                    shot_number=1,
                    start_time_sec=0,
                    end_time_sec=2,
                    keyframe_paths='["three.jpg"]',
                    status="completed",
                    analysis_text="第三镜分析",
                    techniques_json='["逆光"]',
                ),
            ]
            db.add_all(shots)
            await db.flush()
            db.add(Dimension(
                shot_id=shots[1].id,
                dimension_name="构图",
                score=4,
                label="中心构图",
                notes="主体稳定",
            ))
            await db.commit()
            self.shot_ids = [shot.id for shot in shots]

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_add_list_and_remove_are_idempotent(self):
        async with self.session_factory() as db:
            first = await add_reference_board_shot(self.shot_ids[1], db)
            second = await add_reference_board_shot(self.shot_ids[1], db)
            page = await list_reference_board(db=db)

            self.assertEqual(first.shot_id, second.shot_id)
            self.assertEqual(page.total, 1)
            self.assertEqual(page.items[0].job_filename, "first.mp4")
            self.assertEqual(page.items[0].dimensions[0].notes, "主体稳定")

            await remove_reference_board_shot(self.shot_ids[1], db)
            await remove_reference_board_shot(self.shot_ids[1], db)
            empty = await list_reference_board(db=db)

        self.assertEqual(empty.total, 0)

    async def test_list_filters_by_job_and_hides_soft_deleted_sources(self):
        async with self.session_factory() as db:
            await add_reference_board_shot(self.shot_ids[0], db)
            await add_reference_board_shot(self.shot_ids[2], db)

            first_page = await list_reference_board(
                job_id="11111111-1111-1111-1111-111111111111",
                db=db,
            )
            self.assertEqual([item.shot_id for item in first_page.items], [self.shot_ids[0]])

            second_job = await db.get(Job, "22222222-2222-2222-2222-222222222222")
            second_job.deleted_at = datetime.now(timezone.utc)
            await db.commit()
            visible = await list_reference_board(db=db)

        self.assertEqual([item.shot_id for item in visible.items], [self.shot_ids[0]])

    async def test_add_rejects_unknown_shot(self):
        async with self.session_factory() as db:
            with self.assertRaises(HTTPException) as ctx:
                await add_reference_board_shot(9999, db)

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_selected_reference_collection_preserves_request_order(self):
        requested = [self.shot_ids[1], self.shot_ids[2], self.shot_ids[0]]
        async with self.session_factory() as db:
            references = await _collect_storyboard_references(db, [], requested)

        selected = sorted(
            [shot for reference in references for shot in reference["shots"]],
            key=lambda shot: shot["selection_order"],
        )
        self.assertEqual([shot["shot_id"] for shot in selected], requested)
        self.assertTrue(all(reference["shots_are_selected"] for reference in references))
        self.assertIn("构图: 中心构图", selected[0]["analysis_text"])


if __name__ == "__main__":
    unittest.main()
