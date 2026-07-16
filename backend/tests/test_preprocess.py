import asyncio
import json
import os
import sys
import threading
import time
import types
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

import backend.services.preprocess as preprocess_module
from backend.services.preprocess import Preprocessor


class PreprocessorShotTests(unittest.TestCase):
    def test_transcription_skips_silent_video(self):
        with TemporaryDirectory() as tmpdir:
            audio_file = os.path.join(tmpdir, "audio.wav")
            with open(audio_file, "wb") as f:
                f.write(b"stale")

            with (
                patch("backend.services.audio_analyzer.has_audio_stream", return_value=False) as probe,
                patch("backend.services.preprocess.run_cancellable") as run_process,
                patch.object(preprocess_module._whisper_worker, "transcribe") as transcribe,
            ):
                result = Preprocessor()._run_transcription(
                    "silent.mp4",
                    audio_file,
                    "base",
                )
            audio_file_exists = os.path.exists(audio_file)

        self.assertEqual(result, [])
        self.assertFalse(audio_file_exists)
        probe.assert_called_once_with("silent.mp4", None)
        run_process.assert_not_called()
        transcribe.assert_not_called()

    def test_save_analysis_inputs_persists_enriched_shot_data(self):
        shot = {
            "shot_number": 1,
            "start_time_sec": 0.0,
            "end_time_sec": 1.0,
            "duration_sec": 1.0,
            "keyframe_paths": "[]",
            "frame_features": {"mean_brightness": 0.4},
            "frame_features_by_frame": {"start": {"mean_brightness": 0.4}},
            "optical_flow": {"前半段": "固定"},
        }

        with TemporaryDirectory() as tmpdir:
            Preprocessor()._save_analysis_inputs(tmpdir, [shot])
            path = os.path.join(tmpdir, "analysis_inputs.json")
            with open(path, encoding="utf-8") as f:
                stored = json.load(f)

            self.assertFalse(os.path.exists(f"{path}.tmp"))

        self.assertEqual(stored[0]["frame_features_by_frame"], shot["frame_features_by_frame"])
        self.assertEqual(stored[0]["optical_flow"], shot["optical_flow"])

    def test_process_shot_uses_frame_analyzer_optical_flow_keys(self):
        preprocessor = Preprocessor()
        shot = {
            "shot_number": 1,
            "start_time_sec": 0.0,
            "end_time_sec": 1.0,
        }

        def extract_frame(_video_path: str, output_path: str, _timestamp: float, _cancel_check=None):
            Image.new("RGB", (2, 2), color=(255, 255, 255)).save(output_path)

        def thumbnail(frame_path: str, thumb_path: str):
            Image.open(frame_path).save(thumb_path)
            return thumb_path

        flow_values = [
            {
                "camera_movement": "pan-right",
                "motion_magnitude": 1.25,
                "motion_std": 0.1,
                "dominant_angle_deg": 0.0,
            },
            {
                "camera_movement": "tilt-up",
                "motion_magnitude": 0.75,
                "motion_std": 0.2,
                "dominant_angle_deg": 90.0,
            },
        ]

        with TemporaryDirectory() as tmpdir:
            shot_dir = os.path.join(tmpdir, "shot_0001")
            os.makedirs(shot_dir)
            with (
                patch.object(preprocessor, "_extract_frame", side_effect=extract_frame),
                patch.object(preprocessor, "_create_ui_thumbnail", side_effect=thumbnail),
                patch(
                    "backend.services.frame_analyzer.analyze_frame",
                    side_effect=[
                        {"frame": "start"},
                        {"frame": "mid"},
                        {"frame": "end"},
                    ],
                ) as analyze_frame,
                patch("backend.services.frame_analyzer.compute_optical_flow", side_effect=flow_values),
            ):
                preprocessor._process_shot("video.mp4", "job-1", shot, shot_dir)

        self.assertEqual(analyze_frame.call_count, 3)
        self.assertEqual(shot["frame_features"], {"frame": "start"})
        self.assertEqual(
            shot["frame_features_by_frame"],
            {
                "start": {"frame": "start"},
                "mid": {"frame": "mid"},
                "end": {"frame": "end"},
            },
        )
        self.assertEqual(
            shot["optical_flow"],
            {
                "前半段": "pan-right (幅度1.25px)",
                "后半段": "tilt-up (幅度0.75px)",
            },
        )
        self.assertEqual(len(json.loads(shot["keyframe_paths"])), 3)

    def test_whisper_model_cache_is_keyed_by_model_name(self):
        loaded: list[str] = []

        def load_model(name: str):
            loaded.append(name)
            return {"model": name}

        fake_whisper = types.SimpleNamespace(load_model=load_model)
        preprocess_module._whisper_models.clear()

        with patch.dict(sys.modules, {"whisper": fake_whisper}):
            self.assertEqual(preprocess_module._get_whisper_model("base"), {"model": "base"})
            self.assertEqual(preprocess_module._get_whisper_model("small"), {"model": "small"})
            self.assertEqual(preprocess_module._get_whisper_model("base"), {"model": "base"})

        self.assertEqual(loaded, ["base", "small"])
        preprocess_module._whisper_models.clear()

    def test_whisper_worker_terminates_process_when_cancelled(self):
        cancel_event = threading.Event()

        class FakeProcess:
            def __init__(self):
                self.alive = True
                self.terminated = False

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.terminated = True
                self.alive = False

            def kill(self):
                self.alive = False

            def join(self, timeout=None):
                return None

            def close(self):
                return None

        class FakeConnection:
            def send(self, _value):
                return None

            def poll(self, timeout):
                cancel_event.wait(timeout)
                return False

            def close(self):
                return None

        worker = preprocess_module._WhisperWorker()
        process = FakeProcess()
        worker._process = process
        worker._connection = FakeConnection()

        timer = threading.Timer(0.05, cancel_event.set)
        timer.start()
        try:
            with patch.object(worker, "_ensure_started_locked"):
                with self.assertRaises(asyncio.CancelledError):
                    worker.transcribe("audio.wav", "base", cancel_event.is_set)
        finally:
            timer.cancel()

        self.assertTrue(process.terminated)
        self.assertFalse(process.is_alive())

    def test_whisper_worker_spawn_returns_plain_segment_data(self):
        with TemporaryDirectory() as tmpdir:
            fake_module = os.path.join(tmpdir, "whisper.py")
            with open(fake_module, "w", encoding="utf-8") as f:
                f.write(
                    "class Model:\n"
                    "    def transcribe(self, audio_file):\n"
                    "        return {'segments': [{'start': 0.0, 'end': 1.5, 'text': ' hello '}]}\n"
                    "\n"
                    "def load_model(name):\n"
                    "    return Model()\n"
                )

            worker = preprocess_module._WhisperWorker()
            sys.path.insert(0, tmpdir)
            try:
                segments = worker.transcribe("audio.wav", "base")
            finally:
                worker.close()
                sys.path.remove(tmpdir)

        self.assertEqual(segments, [{"start": 0.0, "end": 1.5, "text": "hello"}])

    def test_whisper_worker_spawn_can_cancel_during_model_load(self):
        with TemporaryDirectory() as tmpdir:
            fake_module = os.path.join(tmpdir, "whisper.py")
            with open(fake_module, "w", encoding="utf-8") as f:
                f.write(
                    "import time\n"
                    "\n"
                    "def load_model(name):\n"
                    "    time.sleep(30)\n"
                )

            worker = preprocess_module._WhisperWorker()
            cancel_event = threading.Event()
            timer = threading.Timer(0.2, cancel_event.set)
            sys.path.insert(0, tmpdir)
            started = time.monotonic()
            timer.start()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    worker.transcribe("audio.wav", "base", cancel_event.is_set)
            finally:
                timer.cancel()
                worker.close()
                sys.path.remove(tmpdir)

        self.assertLess(time.monotonic() - started, 3.0)


class PreprocessorLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_rejects_shot_count_over_limit_before_frame_processing(self):
        shots = [
            {"shot_number": index + 1, "start_time_sec": float(index), "end_time_sec": float(index + 1)}
            for index in range(preprocess_module.MAX_SHOTS + 1)
        ]
        with TemporaryDirectory() as tmpdir:
            with (
                patch("backend.services.preprocess.JOBS_DIR", tmpdir),
                patch.object(Preprocessor, "_detect_shots", return_value=shots),
                patch.object(Preprocessor, "_process_shot") as process_shot,
            ):
                with self.assertRaisesRegex(RuntimeError, "超过单次分析上限"):
                    await Preprocessor().run("job-1", "video.mp4", asyncio.Queue())

        process_shot.assert_not_called()

    async def test_preprocessing_jobs_share_global_capacity_limit(self):
        active = 0
        max_active = 0
        first_entered = asyncio.Event()
        release = asyncio.Event()

        class ControlledPreprocessor(Preprocessor):
            async def _run(self, job_id, video_path, queue, cancel_check=None):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                first_entered.set()
                await release.wait()
                active -= 1
                return [], [], {}

        processor = ControlledPreprocessor()
        first = asyncio.create_task(processor.run("job-1", "one.mp4", asyncio.Queue()))
        await first_entered.wait()
        second = asyncio.create_task(processor.run("job-2", "two.mp4", asyncio.Queue()))
        await asyncio.sleep(0)

        self.assertEqual(active, 1)
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(max_active, 1)

    async def test_queued_preprocessing_job_can_be_cancelled(self):
        first_entered = asyncio.Event()
        release = asyncio.Event()
        cancel_event = threading.Event()

        class ControlledPreprocessor(Preprocessor):
            async def _run(self, job_id, video_path, queue, cancel_check=None):
                first_entered.set()
                await release.wait()
                return [], [], {}

        processor = ControlledPreprocessor()
        first = asyncio.create_task(processor.run("job-1", "one.mp4", asyncio.Queue()))
        await first_entered.wait()
        second = asyncio.create_task(
            processor.run("job-2", "two.mp4", asyncio.Queue(), cancel_event.is_set)
        )
        await asyncio.sleep(0)
        cancel_event.set()

        try:
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(second, timeout=1)
        finally:
            release.set()
            await first

    async def test_frame_workers_exit_before_cancelled_run_returns(self):
        shots = [
            {"shot_number": 1, "start_time_sec": 0.0, "end_time_sec": 1.0},
            {"shot_number": 2, "start_time_sec": 1.0, "end_time_sec": 2.0},
        ]
        cancel_event = threading.Event()
        started = {1: threading.Event(), 2: threading.Event()}
        finished: set[int] = set()
        finished_lock = threading.Lock()

        def process_shot(_video_path, _job_id, shot, _shot_dir, cancel_check=None):
            shot_number = shot["shot_number"]
            started[shot_number].set()
            while not (cancel_check and cancel_check()):
                time.sleep(0.01)
            with finished_lock:
                finished.add(shot_number)
            raise asyncio.CancelledError()

        with TemporaryDirectory() as tmpdir:
            with (
                patch("backend.services.preprocess.JOBS_DIR", tmpdir),
                patch.object(Preprocessor, "_detect_shots", return_value=shots),
                patch("backend.services.audio_analyzer.analyze_audio", return_value={}),
                patch.object(Preprocessor, "_ensure_playback_video", return_value=False),
                patch.object(Preprocessor, "_process_shot", side_effect=process_shot),
            ):
                task = asyncio.create_task(
                    Preprocessor().run("job-1", "video.mp4", asyncio.Queue(), cancel_event.is_set)
                )
                self.assertTrue(await asyncio.to_thread(started[1].wait, 1))
                self.assertTrue(await asyncio.to_thread(started[2].wait, 1))
                cancel_event.set()

                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=1)

        self.assertEqual(finished, {1, 2})


if __name__ == "__main__":
    unittest.main()
