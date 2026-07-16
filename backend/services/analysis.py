from __future__ import annotations

import json
import os
import base64
import asyncio
import sys
from io import BytesIO
from collections.abc import Callable
from typing import Optional
import httpx
from PIL import Image
from sqlalchemy import select
from backend.config import JOBS_DIR, BATCH_SIZE, ANALYSIS_CONCURRENCY, ANALYSIS_IMAGE_MAX_SIDE, ANALYSIS_IMAGE_JPEG_QUALITY
from backend.database import AsyncSessionLocal
from backend.models import Shot
from backend.services.api_runner import AnalysisOutputError, analyze_one_shot, get_analysis_api_config
from backend.services.model_retry import is_retryable_model_error, model_retry_delay


_analysis_semaphore: asyncio.Semaphore | None = None
_analysis_semaphore_loop: asyncio.AbstractEventLoop | None = None


class AnalysisService:
    async def run(self, job_id: str, shots: list[dict], transcript: list[dict],
                  audio_analysis: dict, queue: asyncio.Queue,
                  cancel_check: Optional[Callable[[], bool]] = None):
        job_dir = os.path.join(JOBS_DIR, job_id)
        total_batches = (len(shots) + BATCH_SIZE - 1) // BATCH_SIZE

        # Build audio summary
        audio_text = self._format_audio(audio_analysis)

        template = _load_prompt_template("cinematography.md")
        api_config = await get_analysis_api_config()

        async with httpx.AsyncClient(timeout=600) as client:
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
                    frame_features = shot.get("frame_features_by_frame")
                    flow = shot.get("optical_flow", {})

                    shot_info = f"## SHOT {sn} (时长 {dur:.1f}s)\n"
                    shot_info += f"数值特征: {_format_frame_features(frame_features, features)}\n"
                    shot_info += f"运镜数据: 前半段={flow.get('前半段', 'N/A')}, 后半段={flow.get('后半段', 'N/A')}"

                    prompt_text = template.replace("{SHOTS}", shot_info)
                    prompt_text = prompt_text.replace("{AUDIO}", audio_text)
                    prompt_text = prompt_text.replace(
                        "{TRANSCRIPT}",
                        _format_transcript_for_shot(
                            transcript,
                            float(shot.get("start_time_sec", 0)),
                            float(shot.get("end_time_sec", 0)),
                        ),
                    )

                    content = await asyncio.to_thread(_load_keyframe_images, job_dir, sn)
                    content.append({"type": "text", "text": prompt_text})

                    last_err = None
                    for attempt in range(3):
                        try:
                            self._raise_if_cancelled(cancel_check)
                            async with _get_analysis_semaphore():
                                return await analyze_one_shot(
                                    sn,
                                    content,
                                    queue,
                                    client=client,
                                    api_config=api_config,
                                    job_id=job_id,
                                    attempt=attempt + 1,
                                )
                        except Exception as e:
                            last_err = e
                            if attempt >= 2 or not is_retryable_model_error(e):
                                raise
                            await asyncio.sleep(model_retry_delay(e, attempt))
                    raise last_err

                results = await asyncio.gather(
                    *[analyze_shot(s) for s in batch_shots],
                    return_exceptions=True,
                )

                for i, result in enumerate(results):
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    if isinstance(result, Exception):
                        shot_num = batch_shots[i]["shot_number"]
                        if isinstance(result, AnalysisOutputError) and result.raw_text:
                            await asyncio.to_thread(
                                self._save_parse_failure,
                                job_id,
                                shot_num,
                                result.raw_text,
                            )
                        await self._mark_failed(job_id, shot_num, str(result))
                        await queue.put({
                            "event": "shot_error",
                            "data": {"shot_number": shot_num, "error": str(result)[:200]}
                        })
                        continue
                    if isinstance(result, dict) and result.get("shots"):
                        await self._save_batch(job_id, result, queue)
                    else:
                        shot_num = batch_shots[i]["shot_number"]
                        raw_text = result.get("raw") if isinstance(result, dict) else None
                        message = "模型未返回镜头分析结果"
                        if raw_text:
                            await asyncio.to_thread(self._save_parse_failure, job_id, shot_num, raw_text)
                            message = "模型输出格式异常，已保留原始响应"
                        await self._mark_failed(job_id, shot_num, message)
                        await queue.put({
                            "event": "shot_error",
                            "data": {"shot_number": shot_num, "error": message}
                        })

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

    def _save_parse_failure(self, job_id: str, shot_number: int, raw_text: str):
        failure_dir = os.path.join(JOBS_DIR, job_id, "parse_failures")
        os.makedirs(failure_dir, exist_ok=True)
        path = os.path.join(failure_dir, f"shot_{shot_number:04d}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw_text)

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
            img_b64 = _encode_keyframe_for_analysis(fp)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
            })
    return content


def _format_transcript_for_shot(
    transcript: list[dict],
    shot_start: float,
    shot_end: float,
    context_sec: float = 2.0,
    max_chars: int = 2000,
) -> str:
    lines = []
    window_start = shot_start - context_sec
    window_end = shot_end + context_sec
    for segment in transcript:
        start = float(segment.get("start", segment.get("start_sec", 0)) or 0)
        end = float(segment.get("end", segment.get("end_sec", start)) or start)
        text = str(segment.get("text", "")).strip()
        if text and end >= window_start and start <= window_end:
            lines.append(f"[{start:.1f}s-{end:.1f}s] {text}")

    if not lines:
        return "（当前镜头无对白或旁白）"
    return "\n".join(lines)[:max_chars]


def _get_analysis_semaphore() -> asyncio.Semaphore:
    global _analysis_semaphore, _analysis_semaphore_loop
    loop = asyncio.get_running_loop()
    if _analysis_semaphore is None or _analysis_semaphore_loop is not loop:
        _analysis_semaphore = asyncio.Semaphore(max(1, ANALYSIS_CONCURRENCY))
        _analysis_semaphore_loop = loop
    return _analysis_semaphore


def _encode_keyframe_for_analysis(image_path: str) -> str:
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            img.thumbnail((ANALYSIS_IMAGE_MAX_SIDE, ANALYSIS_IMAGE_MAX_SIDE), resampling)
            buffer = BytesIO()
            img.save(
                buffer,
                format="JPEG",
                quality=ANALYSIS_IMAGE_JPEG_QUALITY,
                optimize=True,
            )
        return base64.b64encode(buffer.getvalue()).decode()
    except Exception:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode()


def _load_prompt_template(filename: str) -> str:
    bundle_root = getattr(sys, "_MEIPASS", None)
    candidates = []
    if bundle_root:
        candidates.append(os.path.join(bundle_root, "backend", "prompts", filename))
    candidates.append(os.path.join(os.path.dirname(__file__), "..", "prompts", filename))

    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()

    checked = ", ".join(os.path.abspath(path) for path in candidates)
    raise FileNotFoundError(f"Prompt template not found: {filename}. Checked: {checked}")


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


def _format_frame_features(frame_features: object, fallback: dict) -> str:
    if not isinstance(frame_features, dict):
        return _format_features(fallback)
    labels = {
        "start": "起始",
        "mid": "中段",
        "end": "结尾",
    }
    parts = []
    for key in ("start", "mid", "end"):
        value = frame_features.get(key)
        if isinstance(value, dict) and value:
            parts.append(f"{labels[key]}[{_format_features(value)}]")
    return "；".join(parts) if parts else _format_features(fallback)
