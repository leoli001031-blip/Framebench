import json
import os
import base64
import asyncio
from collections.abc import Callable
from typing import Optional
from sqlalchemy import select
from backend.config import JOBS_DIR, BATCH_SIZE
from backend.database import AsyncSessionLocal
from backend.models import Shot
from backend.services.api_runner import analyze_one_shot


class AnalysisService:
    async def run(self, job_id: str, shots: list[dict], transcript: list[dict],
                  audio_analysis: dict, queue: asyncio.Queue,
                  cancel_check: Optional[Callable[[], bool]] = None):
        job_dir = os.path.join(JOBS_DIR, job_id)
        total_batches = (len(shots) + BATCH_SIZE - 1) // BATCH_SIZE

        transcript_texts = [f"[{seg['start']:.1f}s-{seg['end']:.1f}s] {seg['text']}" for seg in transcript]
        full_transcript = "\n".join(transcript_texts)

        # Build audio summary
        audio_text = self._format_audio(audio_analysis)

        prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "cinematography.md")
        with open(prompt_path, "r") as f:
            template = f.read()

        for batch_idx in range(0, len(shots), BATCH_SIZE):
            self._raise_if_cancelled(cancel_check)
            batch_shots = shots[batch_idx:batch_idx + BATCH_SIZE]
            batch_id = batch_idx // BATCH_SIZE + 1
            shot_numbers = [s["shot_number"] for s in batch_shots]

            await queue.put({
                "event": "shot_start",
                "data": {"batch": batch_id, "total_batches": total_batches, "shot_numbers": shot_numbers}
            })

            async def analyze_shot(shot: dict) -> dict:
                self._raise_if_cancelled(cancel_check)
                sn = shot["shot_number"]
                dur = shot.get("duration_sec", shot["end_time_sec"] - shot["start_time_sec"])
                features = shot.get("frame_features") or {}
                flow = shot.get("optical_flow", {})

                shot_info = f"## SHOT {sn} (时长 {dur:.1f}s)\n"
                shot_info += f"数值特征: {_format_features(features)}\n"
                shot_info += f"运镜数据: 前半段={flow.get('前半段', 'N/A')}, 后半段={flow.get('后半段', 'N/A')}"

                prompt_text = template.replace("{SHOTS}", shot_info)
                prompt_text = prompt_text.replace("{AUDIO}", audio_text)
                prompt_text = prompt_text.replace("{TRANSCRIPT}", full_transcript[:3000])

                # Encode all 3 keyframes (start, mid, end) — offload file I/O
                content = await asyncio.to_thread(
                    _load_keyframe_images, job_dir, sn
                )
                content.append({"type": "text", "text": prompt_text})

                # Retry up to 2 times
                last_err = None
                for attempt in range(3):
                    try:
                        self._raise_if_cancelled(cancel_check)
                        return await analyze_one_shot(sn, content, queue)
                    except Exception as e:
                        last_err = e
                        if attempt < 2:
                            await asyncio.sleep(3)
                raise last_err

            results = await asyncio.gather(
                *[analyze_shot(s) for s in batch_shots],
                return_exceptions=True,
            )

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    shot_num = batch_shots[i]["shot_number"]
                    await self._mark_failed(job_id, shot_num, str(result))
                    await queue.put({
                        "event": "shot_error",
                        "data": {"shot_number": shot_num, "error": str(result)[:200]}
                    })
                    continue
                if result and result.get("shots"):
                    await self._save_batch(job_id, result, queue)

            progress = 0.3 + 0.7 * (batch_id / total_batches)
            async with AsyncSessionLocal() as db:
                from backend.models import Job
                result_db = await db.execute(select(Job).where(Job.id == job_id))
                job = result_db.scalar_one()
                job.progress = progress
                await db.commit()

    def _raise_if_cancelled(self, cancel_check: Optional[Callable[[], bool]]):
        if cancel_check and cancel_check():
            raise asyncio.CancelledError()

    async def _mark_failed(self, job_id: str, shot_number: int, error: str):
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Shot).where(Shot.job_id == job_id, Shot.shot_number == shot_number)
            )
            shot = result.scalar_one_or_none()
            if shot and shot.status != "completed":
                shot.status = "failed"
                shot.overall_notes = f"分析失败: {error[:300]}"
                await db.commit()

    def _format_audio(self, a: dict) -> str:
        if not a or "error" in a:
            return "（无音频数据）"
        parts = [
            f"BPM: {a.get('bpm', '?')}, 拍密度: {a.get('beat_density', '?')}拍/秒",
            f"能量曲线: {a.get('energy_curve', '?')}",
            f"音色亮度: {a.get('brightness', '?')}",
            f"分段: {'; '.join(a.get('segments', []))}",
        ]
        return "\n".join(parts)

    async def _save_batch(self, job_id: str, result: dict, queue: asyncio.Queue):
        async with AsyncSessionLocal() as db:
            for shot_data in result.get("shots", []):
                sn = shot_data["shot_number"]
                shot_result = await db.execute(
                    select(Shot).where(Shot.job_id == job_id, Shot.shot_number == sn)
                )
                shot = shot_result.scalar_one_or_none()
                if not shot or shot.status == "completed":
                    continue

                shot.status = "completed"
                shot.overall_notes = shot_data.get("analysis", "")
                shot.analysis_text = shot_data.get("analysis", "")
                shot.techniques_json = json.dumps(
                    shot_data.get("techniques_to_reference", []),
                    ensure_ascii=False,
                )

                await queue.put({
                    "event": "shot_done",
                    "data": {
                        "shot_number": sn,
                        "analysis": shot_data.get("analysis", ""),
                        "techniques": shot_data.get("techniques_to_reference", []),
                    }
                })

            await db.commit()


def _load_keyframe_images(job_dir: str, shot_number: int) -> list[dict]:
    """Read and base64-encode keyframe images for a shot (blocking I/O)."""
    content = []
    frame_dir = os.path.join(job_dir, "frames", f"shot_{shot_number:04d}")
    for fn in ["frame_start.jpg", "frame_mid.jpg", "frame_end.jpg"]:
        fp = os.path.join(frame_dir, fn)
        if os.path.exists(fp):
            with open(fp, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
            })
    return content


def _format_features(f: dict) -> str:
    if not f:
        return "无"
    dc = f.get("dominant_colors", [])
    color_strs = ", ".join([f"{c['hex']}({c['pct']:.0%})" for c in dc[:3]])
    return (
        f"亮度={f.get('mean_brightness','?')}, 对比度={f.get('contrast_std','?')}, "
        f"色温={f.get('color_temperature','?')}, 饱和度={f.get('saturation_mean','?')}, "
        f"主色={color_strs}, 熵={f.get('entropy','?')}"
    )
