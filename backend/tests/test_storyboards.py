import base64
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.database import Base
from backend.models import Job, Shot, Storyboard, StoryboardGenerationTask, StoryboardShot, SystemSetting
from backend.routers import jobs
from backend.routers.jobs import _serialize_setting, list_storyboards
from backend.schemas import GenerateStoryboardRequest, StoryboardShotDetail
from backend.services.secret_store import KEYCHAIN_MARKER
from backend.services.storyboard_images import (
    StoryboardImageConfig,
    _build_storyboard_image_prompt,
    generate_storyboard_shot_image,
)
from backend.services.storyboard_generator import (
    _build_reference_context,
    generate_storyboard as generate_storyboard_text,
)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADggGOSHzRgAAAAABJRU5ErkJggg=="
)


class StoryboardTests(unittest.IsolatedAsyncioTestCase):
    def test_reference_context_samples_long_source_instead_of_dumping_every_shot(self):
        references = [{
            "filename": "long.mp4",
            "category": "广告",
            "overview_text": "总体风格",
            "shots": [
                {"shot_number": number, "duration_sec": 1, "analysis_text": f"分析 {number}"}
                for number in range(1, 21)
            ],
        }]

        context, used_shots = _build_reference_context(references)

        self.assertEqual(used_shots, 8)
        self.assertEqual(context.count("- 镜头"), 8)
        self.assertIn("镜头 1", context)
        self.assertIn("镜头 20", context)
        self.assertLessEqual(len(context), 18000)

    def test_reference_context_preserves_every_source_under_budget(self):
        references = [
            {
                "filename": f"source-{source}.mp4",
                "category": "广告",
                "overview_text": "风格说明" * 200,
                "shots": [
                    {"shot_number": shot, "duration_sec": 1, "analysis_text": f"来源 {source} 分析 {shot}" * 20}
                    for shot in range(1, 21)
                ],
            }
            for source in range(1, 21)
        ]

        context, used_shots = _build_reference_context(references)

        for source in range(1, 21):
            self.assertIn(f"source-{source}.mp4", context)
        self.assertEqual(used_shots, context.count("- 镜头"))
        self.assertLessEqual(len(context), 18000)

    async def test_storyboard_text_generation_requests_json_object(self):
        captured: dict[str, object] = {}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, url, **kwargs):
                captured["url"] = url
                captured["payload"] = kwargs["json"]
                return httpx.Response(200, json={
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({
                                "title": "测试分镜",
                                "full_notes": "",
                                "total_duration_sec": 2,
                                "shots": [{
                                    "shot_number": 1,
                                    "duration_sec": 2,
                                    "description": "测试画面",
                                    "camera_movement": "固定",
                                    "bgm_note": "",
                                    "reference_from": "",
                                    "image_prompt": "Wide shot",
                                }],
                            }, ensure_ascii=False),
                        }
                    }]
                })

        async def fake_setting(key, default=""):
            if key.endswith("api_key"):
                return "test-key"
            return default

        references = [{
            "filename": "reference.mp4",
            "category": "广告",
            "overview_text": "参考综述",
            "shots": [{"shot_number": 1, "duration_sec": 2, "analysis_text": "参考分析"}],
            "all_techniques": [],
        }]
        with (
            patch("backend.services.storyboard_generator.httpx.AsyncClient", return_value=FakeClient()),
            patch("backend.services.storyboard_generator.get_system_setting", new=fake_setting),
        ):
            result = await generate_storyboard_text("测试需求", references, 2)

        self.assertEqual(result["title"], "测试分镜")
        self.assertEqual(captured["url"], "https://api.stepfun.com/v1/chat/completions")
        self.assertEqual(captured["payload"]["response_format"], {"type": "json_object"})

    async def test_storyboard_text_generation_rejects_missing_required_fields(self):
        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, _url, **_kwargs):
                return httpx.Response(200, json={
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({
                            "title": "不完整分镜",
                            "full_notes": "",
                            "total_duration_sec": 2,
                            "shots": [{"shot_number": 1, "duration_sec": 2}],
                        }, ensure_ascii=False)},
                    }],
                })

        async def fake_setting(key, default=""):
            if key.endswith("api_key"):
                return "test-key"
            return default

        with (
            patch("backend.services.storyboard_generator.httpx.AsyncClient", return_value=FakeClient()),
            patch("backend.services.storyboard_generator.get_system_setting", new=fake_setting),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await generate_storyboard_text("测试需求", [{
                    "filename": "reference.mp4",
                    "shots": [{"shot_number": 1, "analysis_text": "参考分析"}],
                }], 2)

        self.assertIn("结构", str(ctx.exception))

    async def test_storyboard_text_generation_retries_429_then_succeeds(self):
        responses = [
            httpx.Response(429, text="limited", headers={"retry-after": "0"}),
            httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({
                        "title": "重试成功",
                        "full_notes": "",
                        "total_duration_sec": 2,
                        "shots": [{
                            "shot_number": 1,
                            "duration_sec": 2,
                            "description": "测试画面",
                            "camera_movement": "固定",
                            "bgm_note": "",
                            "reference_from": "",
                            "image_prompt": "Wide shot",
                        }],
                    }, ensure_ascii=False)},
                }],
            }),
        ]

        class FakeClient:
            calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, _url, **_kwargs):
                response = responses[self.calls]
                self.calls += 1
                return response

        fake_client = FakeClient()

        async def fake_setting(key, default=""):
            return "test-key" if key.endswith("api_key") else default

        with (
            patch("backend.services.storyboard_generator.httpx.AsyncClient", return_value=fake_client),
            patch("backend.services.storyboard_generator.get_system_setting", new=fake_setting),
            patch("backend.services.storyboard_generator.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            result = await generate_storyboard_text("测试需求", [{
                "filename": "reference.mp4",
                "shots": [{"shot_number": 1, "analysis_text": "参考分析"}],
            }], 2)

        self.assertEqual(result["title"], "重试成功")
        self.assertEqual(fake_client.calls, 2)
        sleep.assert_awaited_once_with(0.0)

    async def test_storyboard_text_generation_does_not_retry_401(self):
        class FakeClient:
            calls = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, _url, **_kwargs):
                self.calls += 1
                return httpx.Response(401, text="unauthorized")

        fake_client = FakeClient()

        async def fake_setting(key, default=""):
            return "test-key" if key.endswith("api_key") else default

        with (
            patch("backend.services.storyboard_generator.httpx.AsyncClient", return_value=fake_client),
            patch("backend.services.storyboard_generator.get_system_setting", new=fake_setting),
        ):
            with self.assertRaisesRegex(RuntimeError, "401"):
                await generate_storyboard_text("测试需求", [{
                    "filename": "reference.mp4",
                    "shots": [{"shot_number": 1, "analysis_text": "参考分析"}],
                }], 2)

        self.assertEqual(fake_client.calls, 1)

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

    async def test_generate_storyboard_image_saves_b64_image(self):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG_1X1).decode("ascii")}]})

        config = StoryboardImageConfig(
            model="step-image-edit-2",
            base_url="https://api.stepfun.com/v1",
            api_key="test-key",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with TemporaryDirectory() as tmpdir:
                generated = await generate_storyboard_shot_image(
                    storyboard_id="storyboard-1",
                    shot_number=7,
                    prompt=(
                        "Wide restaurant scene, warm orange-yellow light, red sign, vivid blue "
                        "chairs, photorealistic, high fidelity --ar 16:9"
                    ),
                    config=config,
                    jobs_dir=tmpdir,
                    client=client,
                )

                self.assertIsNotNone(generated)
                self.assertEqual(generated.image_url, "/api/frames/storyboards/storyboard-1/shot_0007.png")
                self.assertTrue(os.path.exists(generated.file_path or ""))
                with open(generated.file_path or "", "rb") as f:
                    self.assertEqual(f.read(), PNG_1X1)

        self.assertEqual(captured["url"], "https://api.stepfun.com/v1/images/generations")
        payload = captured["payload"]
        self.assertEqual(payload["model"], "step-image-edit-2")
        self.assertEqual(payload["size"], "768x1360")
        self.assertEqual(payload["response_format"], "b64_json")
        self.assertEqual(payload["steps"], 8)
        self.assertEqual(payload["cfg_scale"], 3.0)
        self.assertFalse(payload["text_mode"])
        self.assertIn("Exactly one uninterrupted 16:9 camera view", payload["prompt"])
        self.assertIn("proportionate human figures and natural gesture", payload["prompt"])
        self.assertIn("Wide restaurant scene", payload["prompt"])
        self.assertNotIn("photorealistic", payload["prompt"].lower())
        self.assertNotIn("high fidelity", payload["prompt"].lower())
        self.assertNotIn("--ar", payload["prompt"].lower())
        self.assertNotIn("orange", payload["prompt"].lower())
        self.assertNotIn("yellow", payload["prompt"].lower())
        self.assertNotIn("vivid", payload["prompt"].lower())
        self.assertNotIn("blue", payload["prompt"].lower())
        self.assertNotRegex(payload["prompt"].lower(), r"\b(?:warm|red)\b")
        self.assertIn("multiple panels", payload["negative_prompt"])
        self.assertIn("photorealistic", payload["negative_prompt"])
        self.assertIn("cartoon", payload["negative_prompt"])

    def test_storyboard_image_prompt_preserves_render_rules_when_truncated(self):
        prompt = _build_storyboard_image_prompt("x" * 1000)

        self.assertEqual(len(prompt), 512)
        self.assertTrue(prompt.endswith(
            "Convert every color mentioned into gray values; keep the drawing loose, unfinished "
            "and fully monochrome."
        ))

    async def test_generate_storyboard_image_truncates_prompt(self):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG_1X1).decode("ascii")}]})

        config = StoryboardImageConfig(
            model="step-image-edit-2",
            base_url="https://api.stepfun.com/v1",
            api_key="test-key",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with TemporaryDirectory() as tmpdir:
                await generate_storyboard_shot_image(
                    storyboard_id="storyboard-1",
                    shot_number=1,
                    prompt="x" * 600,
                    config=config,
                    jobs_dir=tmpdir,
                    client=client,
                )

        self.assertEqual(len(captured["payload"]["prompt"]), 512)

    async def test_generate_storyboard_image_uses_generic_openai_payload_for_custom_provider(self):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"data": [{"url": "https://images.example.test/shot.png"}]})

        config = StoryboardImageConfig(
            model="custom-image-model",
            base_url="https://provider.example.test/v1",
            api_key="test-key",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            generated = await generate_storyboard_shot_image(
                storyboard_id="storyboard-1",
                shot_number=1,
                prompt="Wide restaurant scene",
                config=config,
                client=client,
            )

        self.assertIsNotNone(generated)
        self.assertEqual(generated.image_url, "https://images.example.test/shot.png")
        self.assertEqual(captured["url"], "https://provider.example.test/v1/images/generations")
        payload = captured["payload"]
        self.assertEqual(set(payload), {"model", "prompt", "n"})
        self.assertEqual(payload["model"], "custom-image-model")
        self.assertEqual(payload["n"], 1)

    async def test_generate_storyboard_image_skips_without_api_key(self):
        config = StoryboardImageConfig(
            model="step-image-edit-2",
            base_url="https://api.stepfun.com/v1",
            api_key="",
        )

        generated = await generate_storyboard_shot_image(
            storyboard_id="storyboard-1",
            shot_number=1,
            prompt="cinematic landscape",
            config=config,
        )

        self.assertIsNone(generated)

    def test_storyboard_shot_detail_exposes_image_url(self):
        row = StoryboardShot(
            storyboard_id="storyboard-1",
            shot_number=1,
            duration_sec=4,
            description="第一镜",
            camera_movement="固定",
            bgm_note="",
            reference_from="",
            image_url="/api/frames/storyboards/storyboard-1/shot_0001.png",
        )

        detail = StoryboardShotDetail.model_validate(row)

        self.assertEqual(detail.image_url, "/api/frames/storyboards/storyboard-1/shot_0001.png")

    def test_image_api_key_is_serialized_as_secret(self):
        setting = SystemSetting(
            key="image_api_key",
            value="secret-value",
            description="分镜图生成 API 密钥",
            updated_at=datetime.now(timezone.utc),
        )

        response = _serialize_setting(setting)

        self.assertTrue(response.is_secret)
        self.assertTrue(response.value.startswith("••••••••"))

    def test_keychain_marker_is_serialized_as_secret(self):
        setting = SystemSetting(
            key="image_api_key",
            value=KEYCHAIN_MARKER,
            description="分镜图生成 API 密钥",
            updated_at=datetime.now(timezone.utc),
        )

        response = _serialize_setting(setting)

        self.assertTrue(response.is_secret)
        self.assertEqual(response.value, "••••••••")

    async def test_duplicate_client_task_id_does_not_start_a_second_generation(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        task_id = "11111111-1111-1111-1111-111111111111"
        async with session_factory() as db:
            db.add(StoryboardGenerationTask(
                id=task_id,
                brief="existing",
                reference_job_ids="[]",
                status="generating",
            ))
            await db.commit()

        with patch.object(jobs, "AsyncSessionLocal", session_factory):
            with self.assertRaises(HTTPException) as ctx:
                await jobs.generate_storyboard(GenerateStoryboardRequest(
                    brief="retry",
                    reference_job_ids=["22222222-2222-2222-2222-222222222222"],
                    client_task_id=task_id,
                ))

        await engine.dispose()
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_selected_shots_can_generate_script_without_batch_images(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            job = Job(
                id="33333333-3333-3333-3333-333333333333",
                filename="reference.mp4",
                video_path="/tmp/reference.mp4",
                status="completed",
            )
            db.add(job)
            await db.flush()
            shot = Shot(
                job_id=job.id,
                shot_number=1,
                start_time_sec=0,
                end_time_sec=2,
                keyframe_paths="[]",
                status="completed",
                analysis_text="参考分析",
                techniques_json="[]",
            )
            db.add(shot)
            await db.commit()
            shot_id = shot.id

        generated = {
            "title": "快速分镜",
            "full_notes": "",
            "total_duration_sec": 2,
            "shots": [{
                "shot_number": 1,
                "duration_sec": 2,
                "description": "测试画面",
                "camera_movement": "固定",
                "bgm_note": "",
                "reference_from": "镜头 1",
                "image_prompt": "Wide shot",
            }],
        }
        task_id = "44444444-4444-4444-4444-444444444444"
        with (
            patch.object(jobs, "AsyncSessionLocal", session_factory),
            patch(
                "backend.services.storyboard_generator.generate_storyboard",
                new=AsyncMock(return_value=generated),
            ),
            patch(
                "backend.services.storyboard_images.get_storyboard_image_config",
                new=AsyncMock(),
            ) as image_config,
        ):
            response = await jobs.generate_storyboard(GenerateStoryboardRequest(
                brief="快速测试",
                reference_shot_ids=[shot_id],
                generate_images=False,
                client_task_id=task_id,
            ))
            chunks = [chunk async for chunk in response.body_iterator]

        async with session_factory() as db:
            storyboard = (await db.execute(select(Storyboard))).scalar_one()
            storyboard_shot = (await db.execute(select(StoryboardShot))).scalar_one()
            task = await db.get(StoryboardGenerationTask, task_id)

        await engine.dispose()
        self.assertTrue(any("event: complete" in str(chunk) for chunk in chunks))
        self.assertEqual(json.loads(storyboard.reference_shot_ids), [shot_id])
        self.assertEqual(json.loads(task.reference_shot_ids), [shot_id])
        self.assertEqual(storyboard_shot.image_status, "pending")
        image_config.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
