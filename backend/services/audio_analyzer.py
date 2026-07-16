"""Audio analysis for BGM, energy, and spectral features."""
import asyncio
import os
from collections.abc import Callable
from typing import Optional
import numpy as np
import librosa
from backend.config import FFMPEG_BIN, FFPROBE_BIN
from backend.services.cancellable_process import run_cancellable


def has_audio_stream(
    video_path: str,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> bool:
    """Return whether the media contains a usable primary audio stream."""
    result = run_cancellable(
        [
            FFPROBE_BIN,
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            video_path,
        ],
        timeout=30,
        cancel_check=cancel_check,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Audio probe failed: {result.stderr}")
    return bool(result.stdout.strip())


def analyze_audio(
    video_path: str,
    job_dir: str,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> dict:
    """Extract audio features from video for BGM-aware analysis.

    Returns a dict with BPM, energy curve, spectral features, and segment description.
    """
    audio_path = os.path.join(job_dir, "audio.wav")

    # Extract audio if not already done
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
        if not has_audio_stream(video_path, cancel_check):
            _remove_file(audio_path)
            return {"error": "no audio"}
        try:
            result = run_cancellable(
                [FFMPEG_BIN, "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
                 "-ar", "16000", "-ac", "1", audio_path],
                timeout=120,
                cancel_check=cancel_check,
            )
        except BaseException:
            _remove_file(audio_path)
            raise
        if result.returncode != 0:
            _remove_file(audio_path)
            raise RuntimeError(f"Audio extraction failed: {result.stderr}")

    if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
        return {"error": "no audio"}

    _raise_if_cancelled(cancel_check)
    try:
        y, sr = librosa.load(audio_path, sr=None)
        duration = len(y) / sr
    except Exception:
        return {"error": "audio load failed"}
    _raise_if_cancelled(cancel_check)

    if duration < 0.5:
        return {"error": "audio too short"}

    # --- BPM ---
    try:
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        bpm = round(float(tempo))
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        beat_count = len(beat_times)
    except Exception:
        bpm = 0
        beat_times = np.array([])
        beat_count = 0
    _raise_if_cancelled(cancel_check)

    # --- Energy curve ---
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    rms_norm = rms / (rms.max() + 1e-6)
    _raise_if_cancelled(cancel_check)

    # Segment energy into 4 phases
    n = len(rms_norm)
    quarters = [
        float(np.mean(rms_norm[i * n // 4: (i + 1) * n // 4]))
        for i in range(4)
    ]

    # Describe energy curve
    if quarters[0] < quarters[-1] * 0.8:
        energy_curve = "渐强型（前低后高）"
    elif quarters[0] > quarters[-1] * 1.2:
        energy_curve = "渐弱型（前高后低）"
    elif max(quarters) > min(quarters) * 1.5:
        energy_curve = "起伏型（中间有高潮）"
    else:
        energy_curve = "平稳型（能量均匀）"

    # --- Spectral centroid (brightness) ---
    try:
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        mean_centroid = round(float(np.mean(centroid)), 1)
        if mean_centroid > 2500:
            brightness = "明亮/高频丰富"
        elif mean_centroid > 1200:
            brightness = "中等亮度"
        else:
            brightness = "偏暗/低频为主"
    except Exception:
        mean_centroid = 0
        brightness = "未知"
    _raise_if_cancelled(cancel_check)

    # --- Segment description ---
    segments = []
    seg_boundaries = np.linspace(0, duration, 5)
    for i in range(4):
        t0, t1 = seg_boundaries[i], seg_boundaries[i + 1]
        seg_energy = quarters[i]
        beats_in_seg = len([b for b in beat_times if t0 <= b < t1])
        seg_label = f"{t0:.0f}s-{t1:.0f}s: "
        if seg_energy > 0.6:
            seg_label += f"高能量段（{beats_in_seg}拍）"
        elif seg_energy > 0.3:
            seg_label += f"中能量段（{beats_in_seg}拍）"
        else:
            seg_label += f"低能量段（{beats_in_seg}拍）"
        segments.append(seg_label)

    return {
        "bpm": bpm,
        "duration_sec": round(duration, 1),
        "energy_curve": energy_curve,
        "brightness": brightness,
        "beat_count": beat_count,
        "beat_density": round(beat_count / max(duration, 1), 1),
        "segments": segments,
        "quarter_energies": [round(q, 3) for q in quarters],
    }


def _raise_if_cancelled(cancel_check: Optional[Callable[[], bool]]) -> None:
    if cancel_check and cancel_check():
        raise asyncio.CancelledError()


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
