import asyncio
import json
import os
import subprocess
import threading
from collections.abc import Callable
from typing import Optional
from backend.config import FFMPEG_BIN, FFPROBE_BIN, JOBS_DIR
from backend.database import AsyncSessionLocal
from backend.models import Job, Shot, TranscriptSegment

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
        shots = await asyncio.to_thread(self._detect_shots, video_path, job_dir)
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
        audio_analysis = await asyncio.to_thread(analyze_audio, video_path, job_dir)
        self._raise_if_cancelled(cancel_check)
        self._save_audio_analysis(job_dir, audio_analysis)

        # Step 3: Extract keyframes + optical flow
        await queue.put({
            "event": "status",
            "data": {"phase": "preprocessing", "step": "frame_extraction", "message": "Extracting keyframes..."}
        })

        for i, shot in enumerate(shots):
            self._raise_if_cancelled(cancel_check)
            sn = shot["shot_number"]
            shot_dir = os.path.join(frames_dir, f"shot_{sn:04d}")
            os.makedirs(shot_dir, exist_ok=True)

            # Offload all blocking work (ffmpeg, cv2, numpy, PIL) to a thread
            await asyncio.to_thread(self._process_shot, video_path, job_id, shot, shot_dir)
            self._raise_if_cancelled(cancel_check)

            if i % 10 == 0:
                await queue.put({
                    "event": "status",
                    "data": {"phase": "preprocessing", "step": "frame_extraction",
                             "progress": i / len(shots), "shot": i, "total": len(shots)}
                })

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
        transcript = await self._transcribe(video_path, job_dir, job_id, queue)

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

        self._extract_frame(video_path, start_frame, start_sec)
        self._extract_frame(video_path, mid_frame, mid_sec)
        end_time = max(start_sec + 0.1, end_sec - 0.15)
        self._extract_frame(video_path, end_frame, end_time)

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

        keyframes = [f"{job_id}/frames/shot_{sn:04d}/frame_start.jpg"]
        if os.path.exists(mid_frame):
            keyframes.append(f"{job_id}/frames/shot_{sn:04d}/frame_mid.jpg")
        if os.path.exists(end_frame):
            keyframes.append(f"{job_id}/frames/shot_{sn:04d}/frame_end.jpg")
        shot["keyframe_paths"] = json.dumps(keyframes)

    def _run_transcription(self, video_path: str, audio_file: str) -> list[dict]:
        """Run ffmpeg audio extraction + whisper transcription (blocking)."""
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
