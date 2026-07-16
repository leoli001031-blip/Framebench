import os
import unittest
import httpx
from datetime import datetime, timezone
from fastapi import HTTPException
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models import Job, SystemSetting
from backend.routers import jobs
from backend.schemas import ConnectivityTestRequest, UpdateSettingRequest
from backend.services.secret_store import KEYCHAIN_MARKER


class DataManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_list_jobs_excludes_deleted_by_default(self):
        async with self.session_factory() as db:
            db.add_all([
                Job(
                    id="11111111-1111-1111-1111-111111111111",
                    filename="active.mp4",
                    video_path="/tmp/active.mp4",
                    status="completed",
                ),
                Job(
                    id="22222222-2222-2222-2222-222222222222",
                    filename="deleted.mp4",
                    video_path="/tmp/deleted.mp4",
                    status="completed",
                    deleted_at=datetime.now(timezone.utc),
                ),
            ])
            await db.commit()

            active_rows = await jobs.list_jobs(db=db)
            deleted_rows = await jobs.list_jobs(db=db, only_deleted=True)

        self.assertEqual([row.filename for row in active_rows], ["active.mp4"])
        self.assertEqual([row.filename for row in deleted_rows], ["deleted.mp4"])

    async def test_data_diagnostics_reports_orphan_job_dirs(self):
        async with self.session_factory() as db:
            known_id = "11111111-1111-1111-1111-111111111111"
            orphan_id = "33333333-3333-3333-3333-333333333333"
            db.add(Job(
                id=known_id,
                filename="known.mp4",
                video_path="/tmp/known.mp4",
                status="completed",
            ))
            await db.commit()

            with TemporaryDirectory() as tmpdir:
                jobs_dir = os.path.join(tmpdir, "data", "jobs")
                known_dir = os.path.join(jobs_dir, known_id)
                orphan_dir = os.path.join(jobs_dir, orphan_id)
                os.makedirs(known_dir)
                os.makedirs(orphan_dir)
                with open(os.path.join(orphan_dir, "report.md"), "w") as f:
                    f.write("# report")
                with open(os.path.join(orphan_dir, "original.mp4"), "wb") as f:
                    f.write(b"video")

                with (
                    patch.object(jobs, "DATA_ROOT", tmpdir),
                    patch.object(jobs, "DB_PATH", os.path.join(tmpdir, "film_master.db")),
                    patch.object(jobs, "JOBS_DIR", jobs_dir),
                    patch.object(jobs, "known_data_roots", return_value=[tmpdir]),
                ):
                    diagnostics = await jobs.get_data_diagnostics(db=db)

        self.assertEqual(diagnostics.active_jobs, 1)
        self.assertEqual(diagnostics.disk_job_dirs, 2)
        self.assertEqual(len(diagnostics.orphan_job_dirs), 1)
        self.assertEqual(diagnostics.orphan_job_dirs[0].id, orphan_id)
        self.assertTrue(diagnostics.orphan_job_dirs[0].has_report)
        self.assertTrue(diagnostics.orphan_job_dirs[0].has_original)
        self.assertEqual(diagnostics.orphan_archive_dir, os.path.join(tmpdir, "data", "_orphans"))

    async def test_rename_category_merges_active_jobs(self):
        async with self.session_factory() as db:
            db.add_all([
                Job(
                    id="11111111-1111-1111-1111-111111111111",
                    filename="a.mp4",
                    video_path="/tmp/a.mp4",
                    status="completed",
                    category="old",
                ),
                Job(
                    id="22222222-2222-2222-2222-222222222222",
                    filename="b.mp4",
                    video_path="/tmp/b.mp4",
                    status="completed",
                    category="keep",
                ),
            ])
            await db.commit()

            from backend.schemas import RenameCategoryRequest

            result = await jobs.rename_category(RenameCategoryRequest(old_name="old", new_name="keep"), db=db)
            categories = await jobs.list_categories(db=db)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(categories.categories, ["keep"])

    async def test_archive_orphan_job_dir_moves_directory(self):
        async with self.session_factory() as db:
            orphan_id = "33333333-3333-3333-3333-333333333333"

            with TemporaryDirectory() as tmpdir:
                jobs_dir = os.path.join(tmpdir, "data", "jobs")
                orphan_dir = os.path.join(jobs_dir, orphan_id)
                os.makedirs(orphan_dir)
                with open(os.path.join(orphan_dir, "original.mp4"), "wb") as f:
                    f.write(b"video")

                with patch.object(jobs, "JOBS_DIR", jobs_dir):
                    result = await jobs.archive_orphan_job_dir(orphan_id, db=db)

                self.assertTrue(result.archived)
                self.assertFalse(os.path.exists(orphan_dir))
                self.assertTrue(os.path.exists(os.path.join(result.archived_path, "original.mp4")))
                self.assertTrue(result.archived_path.startswith(os.path.join(tmpdir, "data", "_orphans")))

    async def test_archive_orphan_job_dir_rejects_known_job(self):
        async with self.session_factory() as db:
            known_id = "11111111-1111-1111-1111-111111111111"
            db.add(Job(
                id=known_id,
                filename="known.mp4",
                video_path="/tmp/known.mp4",
                status="completed",
            ))
            await db.commit()

            with self.assertRaises(HTTPException) as ctx:
                await jobs.archive_orphan_job_dir(known_id, db=db)

        self.assertEqual(ctx.exception.status_code, 409)

    async def test_permanent_delete_keeps_database_record_when_file_removal_fails(self):
        job_id = "44444444-4444-4444-4444-444444444444"
        async with self.session_factory() as db:
            db.add(Job(
                id=job_id,
                filename="delete-me.mp4",
                video_path="/tmp/delete-me.mp4",
                status="completed",
                deleted_at=datetime.now(timezone.utc),
            ))
            await db.commit()

        with TemporaryDirectory() as tmpdir:
            job_dir = os.path.join(tmpdir, job_id)
            os.makedirs(job_dir)
            with (
                patch("backend.database.AsyncSessionLocal", self.session_factory),
                patch.object(jobs, "JOBS_DIR", tmpdir),
                patch("backend.routers.jobs.shutil.rmtree", side_effect=OSError("permission denied")),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await jobs.permanently_delete_job(job_id)

        async with self.session_factory() as db:
            stored = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIsNotNone(stored)

    async def test_cancel_job_registers_signal_before_marking_database_state(self):
        job_id = "55555555-5555-5555-5555-555555555555"
        async with self.session_factory() as db:
            db.add(Job(
                id=job_id,
                filename="running.mp4",
                video_path="/tmp/running.mp4",
                status="analyzing",
            ))
            await db.commit()

        cancel = AsyncMock(return_value=True)
        with (
            patch("backend.database.AsyncSessionLocal", self.session_factory),
            patch.object(jobs.job_manager, "request_cancel", cancel),
            patch.object(jobs.job_manager, "is_running", return_value=True),
        ):
            response = await jobs.cancel_job(job_id)

        async with self.session_factory() as db:
            stored = await db.get(Job, job_id)

        cancel.assert_awaited_once_with(job_id)
        self.assertEqual(response.status, "cancelling")
        self.assertEqual(stored.status, "cancelling")
        self.assertEqual(stored.error_message, "Cancellation requested")

    async def test_late_cancel_does_not_overwrite_completed_job(self):
        job_id = "66666666-6666-6666-6666-666666666666"
        async with self.session_factory() as db:
            db.add(Job(
                id=job_id,
                filename="finished.mp4",
                video_path="/tmp/finished.mp4",
                status="completed",
            ))
            await db.commit()

        with (
            patch("backend.database.AsyncSessionLocal", self.session_factory),
            patch.object(jobs.job_manager, "request_cancel", AsyncMock(return_value=True)),
            patch.object(jobs.job_manager, "is_running", return_value=False),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await jobs.cancel_job(job_id)

        async with self.session_factory() as db:
            stored = await db.get(Job, job_id)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(stored.status, "completed")

    async def test_delete_repairs_stale_running_status_when_task_is_missing(self):
        job_id = "77777777-7777-7777-7777-777777777777"
        async with self.session_factory() as db:
            db.add(Job(
                id=job_id,
                filename="stale.mp4",
                video_path="/tmp/stale.mp4",
                status="preprocessing",
            ))
            await db.commit()

        with (
            patch("backend.database.AsyncSessionLocal", self.session_factory),
            patch.object(jobs.job_manager, "request_cancel", AsyncMock(return_value=False)),
        ):
            response = await jobs.delete_job(job_id)

        async with self.session_factory() as db:
            stored = await db.get(Job, job_id)

        self.assertFalse(response.deleted)
        self.assertEqual(stored.status, "failed")
        self.assertEqual(stored.error_message, "Cancelled by user")

    def test_log_redaction_removes_tokens(self):
        redacted = jobs._redact_log_text(
            'GET /api/jobs?token=abc123SECRET&x=1 HTTP/1.1\n'
            'Authorization: Bearer sk-test-secret\n'
            'X-Framebench-Token: local-secret\n'
        )

        self.assertIn("token=[REDACTED]", redacted)
        self.assertIn("Bearer [REDACTED]", redacted)
        self.assertIn("X-Framebench-Token: [REDACTED]", redacted)
        self.assertNotIn("abc123SECRET", redacted)
        self.assertNotIn("sk-test-secret", redacted)
        self.assertNotIn("local-secret", redacted)

    async def test_update_secret_setting_stores_keychain_marker(self):
        async with self.session_factory() as db:
            with patch("backend.services.secret_store.store_secret_value", return_value=KEYCHAIN_MARKER):
                response = await jobs.update_setting(
                    "analysis_api_key",
                    UpdateSettingRequest(value="secret-value"),
                    db=db,
                )
            stored = await db.get(SystemSetting, "analysis_api_key")

        self.assertEqual(stored.value, KEYCHAIN_MARKER)
        self.assertTrue(response.is_secret)
        self.assertEqual(response.value, "••••••••")

    async def test_update_setting_rejects_invalid_whisper_model(self):
        async with self.session_factory() as db:
            with self.assertRaises(HTTPException) as ctx:
                await jobs.update_setting(
                    "whisper_model",
                    UpdateSettingRequest(value="not-a-model"),
                    db=db,
                )
            stored = await db.get(SystemSetting, "whisper_model")

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIsNone(stored)

    async def test_update_setting_rejects_invalid_base_url(self):
        async with self.session_factory() as db:
            with self.assertRaises(HTTPException) as ctx:
                await jobs.update_setting(
                    "analysis_base_url",
                    UpdateSettingRequest(value="stepfun.local"),
                    db=db,
                )

        self.assertEqual(ctx.exception.status_code, 400)

    async def test_text_connectivity_preserves_remote_http_status(self):
        response = httpx.Response(
            401,
            json={"error": {"message": "invalid token"}},
            request=httpx.Request("POST", "https://api.stepfun.com/v1/chat/completions"),
        )
        with patch.object(jobs.httpx, "AsyncClient") as client_class:
            client_class.return_value.__aenter__.return_value.post = AsyncMock(return_value=response)
            with self.assertRaises(HTTPException) as ctx:
                await jobs.test_connectivity(ConnectivityTestRequest(
                    engine="storyboard",
                    api_key="current-key",
                    model="step-3.7-flash",
                    base_url="https://api.stepfun.com/v1/",
                ))

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("invalid token", ctx.exception.detail)
        call = client_class.return_value.__aenter__.return_value.post.await_args
        self.assertEqual(call.args[0], "https://api.stepfun.com/v1/chat/completions")
        self.assertEqual(call.kwargs["json"]["model"], "step-3.7-flash")


if __name__ == "__main__":
    unittest.main()
