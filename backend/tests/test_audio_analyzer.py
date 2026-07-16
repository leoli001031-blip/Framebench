import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from backend.services.audio_analyzer import analyze_audio, has_audio_stream


class AudioAnalyzerTests(unittest.TestCase):
    def test_silent_video_returns_no_audio_without_extracting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch(
                    "backend.services.audio_analyzer.run_cancellable",
                    return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
                ) as run_process,
                patch("backend.services.audio_analyzer.librosa.load") as load,
            ):
                result = analyze_audio("silent.mp4", tmpdir)

        self.assertEqual(result, {"error": "no audio"})
        self.assertEqual(run_process.call_count, 1)
        self.assertIn("-select_streams", run_process.call_args.args[0])
        load.assert_not_called()

    def test_audio_probe_failure_remains_fatal(self):
        with patch(
            "backend.services.audio_analyzer.run_cancellable",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="invalid media"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Audio probe failed: invalid media"):
                has_audio_stream("broken.mp4")

    def test_reuses_single_beat_analysis_and_preserves_source_sample_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.wav")
            with open(audio_path, "wb") as f:
                f.write(b"0" * 2048)

            with (
                patch("backend.services.audio_analyzer.librosa.load", return_value=(np.ones(16000), 16000)) as load,
                patch(
                    "backend.services.audio_analyzer.librosa.beat.beat_track",
                    return_value=(120.0, np.array([10, 20, 30])),
                ) as beat_track,
                patch(
                    "backend.services.audio_analyzer.librosa.frames_to_time",
                    return_value=np.array([0.5, 1.0, 1.5]),
                ),
                patch(
                    "backend.services.audio_analyzer.librosa.feature.rms",
                    return_value=np.array([[0.2, 0.4, 0.6, 0.8]]),
                ),
                patch(
                    "backend.services.audio_analyzer.librosa.feature.spectral_centroid",
                    return_value=np.array([[1500.0, 1600.0]]),
                ),
            ):
                result = analyze_audio("video.mp4", tmpdir)

        load.assert_called_once_with(audio_path, sr=None)
        self.assertEqual(beat_track.call_count, 1)
        self.assertEqual(result["bpm"], 120)
        self.assertEqual(result["beat_count"], 3)


if __name__ == "__main__":
    unittest.main()
