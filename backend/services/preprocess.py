import asyncio
import json
import os
import subprocess
import threading
from collections.abc import Callable
from typing import Optional
from PIL import Image
from backend.config import (
    FFMPEG_BIN, FFPROBE_BIN, JOBS_DIR,
    UI_FRAME_MAX_SIDE, UI_FRAME_JPEG_QUALITY,
    FRAME_EXTRACTION_CONCURRENCY,
)
from backend.database import AsyncSessionLocal
from backend.models import Job, Shot, TranscriptSegment
from backend.services.perf import directory_size_bytes, perf_now, record_duration

_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper_model():
    """Lazy-load whisper model with thread-safe double-checked locking."""
    global _whisper_model
    if _whisper_model is None:
        with _whisper_lock:
            if _whisper_model is None:
                import whisper
                _whisper_model = whisper.load_model("base")
    return _whisper_model


class Preprocessor:
    async def run(self, job_id: str, video_path: str, queue: asyncio.Queue,
                  cancel_check: Optional[Callable[[], bool]] = None) -> tuple[list[dict], list[dict], dict]:
        job_dir = os.path.join(JOBS_DIR, job_id)
        frames_dir = os.path.join(job_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        # Step 1: Shot detection
        await queue.put({
            "event": "status",
            "data": {"phase": "preprocessing", "step": "shot_detection", "message": "Detecting shots..."}
        })
        phase_started = perf_now()
        shots = await asyncio.to_thread(self._detect_shots, video_path, job_dir)
        record_duration(job_dir, "shot_detection_sec", phase_started, {"shot_count": len(shots)})
        self._raise_if_cancelled(cancel_check)
        await queue.put({
            "event": "status",
            "data": {"phase": "preprocessing", "step": "shot_detection", "message": f"Found {len(shots)} shots"}
        })

        # Step 2: Audio analysis (BGM, energy, etc.)
        await queue.put({
            "event": "status",
            "data": {"phase": "preprocessing", "step": "audio_analysis", "message": "Analyzing audio..."}
        })
        from backend.services.audio_analyzer import analyze_audio
        phase_started = perf_now()
        audio_analysis = await asyncio.to_thread(analyze_audio, video_path, job_dir)
        record_duration(job_dir, "audio_analysis_sec", phase_started)
        self._raise_if_cancelled(cancel_check)
        self._save_audio_analysis(job_dir, audio_analysis)

        await queue.put({
            "event": "status",
            "data": {"phase": "preprocessing", "step": "playback_video", "message": "Preparing playback video..."}
        })
        phase_started = perf_now()
        playback_created = await asyncio.to_thread(self._ensure_playback_video, video_path, job_dir)
        record_duration(job_dir, "playback_video_sec", phase_started, {"playback_video_created": playback_created})
        self._raise_if_cancelled(cancel_check)

        # Step 3: Extract keyframes + optical flow
        await queue.put({
            "event": "status",
            "data": {"phase": "preprocessing", "step": "frame_extraction", "message": "Extracting keyframes..."}
        })

        phase_started = perf_now()
        frame_semaphore = asyncio.Semaphore(max(1, FRAME_EXTRACTION_CONCURRENCY))

        async def process_one_shot(shot: dict):
            self._raise_if_cancelled(cancel_check)
            sn = shot["shot_number"]
            shot_dir = os.path.join(frames_dir, f"shot_{sn:04d}")
            os.makedirs(shot_dir, exist_ok=True)

            async with frame_semaphore:
                self._raise_if_cancelled(cancel_check)
                await asyncio.to_thread(self._process_shot, video_path, job_id, shot, shot_dir)
                self._raise_if_cancelled(cancel_check)

        processed = 0
        tasks = [asyncio.create_task(process_one_shot(shot)) for shot in shots]
        try:
            for task in asyncio.as_completed(tasks):
                await task
                processed += 1
                if processed % 10 == 0 or processed == len(shots):
                    await queue.put({
                        "event": "status",
                        "data": {"phase": "preprocessing", "step": "frame_extraction",
                                 "progress": processed / len(shots), "shot": processed, "total": len(shots)}
                    })
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        frame_bytes = await asyncio.to_thread(directory_size_bytes, frames_dir)
        record_duration(
            job_dir,
            "frame_extraction_sec",
            phase_started,
            {
                "frame_bytes": frame_bytes,
                "frame_extraction_concurrency": max(1, FRAME_EXTRACTION_CONCURRENCY),
            },
        )

        # Save shots to DB
        async with AsyncSessionLocal() as db:
            for shot in shots:
                db_shot = Shot(
                    job_id=job_id,
                    shot_number=shot["shot_number"],
                    start_time_sec=shot["start_time_sec"],
                    end_time_sec=shot["end_time_sec"],
                    keyframe_paths=shot["keyframe_paths"],
                    status="pending",
                )
                db.add(db_shot)
            await db.commit()

        await queue.put({
            "event": "status",
            "data": {"phase": "preprocessing", "step": "frame_extraction", "progress": 1.0,
                     "message": f"Extracted keyframes for {len(shots)} shots"}
        })

        # Step 4: Transcription
        await queue.put({
            "event": "status",
            "data": {"phase": "preprocessing", "step": "transcription", "message": "Transcribing audio..."}
        })
        phase_started = perf_now()
        transcript = await self._transcribe(video_path, job_dir, job_id, queue)
        record_duration(job_dir, "transcription_sec", phase_started, {"transcript_segments": len(transcript)})

        return shots, transcript, audio_analysis

    def _raise_if_cancelled(self, cancel_check: Optional[Callable[[], bool]]):
        if cancel_check and cancel_check():
            raise asyncio.CancelledError()

    def _detect_shots(self, video_path: str, job_dir: str) -> list[dict]:
        shots_file = os.path.join(job_dir, "shots.json")

        result = subprocess.run(
            [
                FFMPEG_BIN, "-i", video_path,
                "-filter:v", "select='gt(scene,0.13)',showinfo",
                "-vsync", "vfr", "-f", "null", "-"
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Shot detection failed: {result.stderr}")
        stderr = result.stderr

        changes = [0.0]
        for line in stderr.split("\n"):
            if "pts_time:" in line:
                try:
                    t_str = line.split("pts_time:")[1].split()[0]
                    t = float(t_str)
                    if t > 0 and t - changes[-1] >= 0.3:
                        changes.append(t)
                except (ValueError, IndexError):
                    continue

        duration = self._get_duration(video_path)
        if changes[-1] < duration:
            changes.append(duration)

        shots = []
        for i in range(len(changes) - 1):
            start = changes[i]
            end = changes[i + 1]
            if end - start >= 0.3:
                shots.append({
                    "shot_number": len(shots) + 1,
                    "start_time_sec": start,
                    "end_time_sec": end,
                })

        with open(shots_file, "w") as f:
            json.dump(shots, f, indent=2)

        return shots

    def _save_audio_analysis(self, job_dir: str, data: dict):
        path = os.path.join(job_dir, "audio_analysis.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _ensure_playback_video(self, video_path: str, job_dir: str) -> bool:
        if self._get_video_codec(video_path) == "h264":
            return False

        playback_path = os.path.join(job_dir, "playback.mp4")
        if os.path.exists(playback_path) and os.path.getsize(playback_path) > 0:
            return True

        tmp_path = os.path.join(job_dir, "playback.tmp.mp4")
        result = subprocess.run(
            [
                FFMPEG_BIN, "-y", "-i", video_path,
                "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", "scale='min(1280,iw)':-2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                tmp_path,
            ],
            capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return False

        os.replace(tmp_path, playback_path)
        return True

    def _get_video_codec(self, video_path: str) -> str:
        result = subprocess.run(
            [
                FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip().lower()

    def _extract_frame(self, video_path: str, output_path: str, time_sec: float):
        result = subprocess.run(
            [
                FFMPEG_BIN, "-y", "-ss", str(time_sec),
                "-i", video_path,
                "-vframes", "1", "-q:v", "2",
                output_path
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Frame extraction failed: {result.stderr}")

    def _create_ui_thumbnail(self, source_path: str, output_path: str) -> str:
        try:
            with Image.open(source_path) as img:
                img = img.convert("RGB")
                resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
                img.thumbnail((UI_FRAME_MAX_SIDE, UI_FRAME_MAX_SIDE), resampling)
                img.save(output_path, "JPEG", quality=UI_FRAME_JPEG_QUALITY, optimize=True)
            return output_path
        except Exception:
            return source_path

    def _process_shot(self, video_path: str, job_id: str, shot: dict, shot_dir: str):
        """Run all blocking per-shot work: frame extraction, analysis, optical flow."""
        from backend.services.frame_analyzer import analyze_frame, compute_optical_flow

        sn = shot["shot_number"]
        start_sec = shot["start_time_sec"]
        end_sec = shot["end_time_sec"]
        dur = end_sec - start_sec
        mid_sec = (start_sec + end_sec) / 2

        start_frame = os.path.join(shot_dir, "frame_start.jpg")
        mid_frame = os.path.join(shot_dir, "frame_mid.jpg")
        end_frame = os.path.join(shot_dir, "frame_end.jpg")
        start_thumb = os.path.join(shot_dir, "frame_start_thumb.jpg")
        mid_thumb = os.path.join(shot_dir, "frame_mid_thumb.jpg")
        end_thumb = os.path.join(shot_dir, "frame_end_thumb.jpg")

        self._extract_frame(video_path, start_frame, start_sec)
        self._extract_frame(video_path, mid_frame, mid_sec)
        end_time = max(start_sec + 0.1, end_sec - 0.15)
        self._extract_frame(video_path, end_frame, end_time)
        start_preview = self._create_ui_thumbnail(start_frame, start_thumb)
        mid_preview = self._create_ui_thumbnail(mid_frame, mid_thumb)
        end_preview = self._create_ui_thumbnail(end_frame, end_thumb)

        frame_features = analyze_frame(start_frame)
        shot["frame_features"] = frame_features
        shot["duration_sec"] = dur

        flow_sm = compute_optical_flow(start_frame, mid_frame)
        flow_me = compute_optical_flow(mid_frame, end_frame)

        if flow_sm and flow_me:
            shot["optical_flow"] = {
                "前半段": f"{flow_sm['dominant_motion']} (幅度{flow_sm['mean_magnitude']}px)",
                "后半段": f"{flow_me['dominant_motion']} (幅度{flow_me['mean_magnitude']}px)",
            }
        else:
            shot["optical_flow"] = {"前半段": "无法计算", "后半段": "无法计算"}

        keyframes = [f"{job_id}/frames/shot_{sn:04d}/{os.path.basename(start_preview)}"]
        if os.path.exists(mid_preview):
            keyframes.append(f"{job_id}/frames/shot_{sn:04d}/{os.path.basename(mid_preview)}")
        if os.path.exists(end_preview):
            keyframes.append(f"{job_id}/frames/shot_{sn:04d}/{os.path.basename(end_preview)}")
        shot["keyframe_paths"] = json.dumps(keyframes)

    def _run_transcription(self, video_path: str, audio_file: str) -> list[dict]:
        """Run ffmpeg audio extraction + whisper transcription (blocking)."""
        if not os.path.exists(audio_file) or os.path.getsize(audio_file) < 1000:
            result = subprocess.run(
                [FFMPEG_BIN, "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
                 "-ar", "16000", "-ac", "1", audio_file],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Audio extraction failed: {result.stderr}")

        segments = []
        try:
            model = _get_whisper_model()
            result = model.transcribe(audio_file)

            for seg in result.get("segments", []):
                segments.append({
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"].strip(),
                })
        except ImportError:
            pass

        return segments

    async def _transcribe(self, video_path: str, job_dir: str, job_id: str, queue: asyncio.Queue) -> list[dict]:
        transcript_file = os.path.join(job_dir, "transcript.json")
        audio_file = os.path.join(job_dir, "audio.wav")

        segments = await asyncio.to_thread(self._run_transcription, video_path, audio_file)

        with open(transcript_file, "w") as f:
            json.dump(segments, f, indent=2)

        async with AsyncSessionLocal() as db:
            for seg in segments:
                db_seg = TranscriptSegment(
                    job_id=job_id,
                    start_sec=seg["start"],
                    end_sec=seg["end"],
                    text=seg["text"],
                )
                db.add(db_seg)
            await db.commit()

        await queue.put({
            "event": "status",
            "data": {"phase": "preprocessing", "step": "transcription",
                     "message": f"Transcribed {len(segments)} segments"}
        })

        return segments

    def _get_duration(self, video_path: str) -> float:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr}")
        try:
            return float(result.stdout.strip())
        except ValueError:
            return 0.0
