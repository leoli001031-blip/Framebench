import os
import tempfile
import unittest
from unittest.mock import patch

from backend.routers import jobs


class VideoPlaybackPathTests(unittest.TestCase):
    def test_prefers_playback_proxy_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_id = "11111111-1111-1111-1111-111111111111"
            job_dir = os.path.join(tmp, job_id)
            os.makedirs(job_dir)
            original = os.path.join(job_dir, "original.mp4")
            playback = os.path.join(job_dir, "playback.mp4")
            with open(original, "wb") as f:
                f.write(b"original")
            with open(playback, "wb") as f:
                f.write(b"playback")

            with patch.object(jobs, "JOBS_DIR", tmp):
                self.assertEqual(jobs._get_playback_video_path(job_id, original), playback)

    def test_falls_back_to_original_without_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_id = "11111111-1111-1111-1111-111111111111"
            job_dir = os.path.join(tmp, job_id)
            os.makedirs(job_dir)
            original = os.path.join(job_dir, "original.mp4")
            with open(original, "wb") as f:
                f.write(b"original")

            with patch.object(jobs, "JOBS_DIR", tmp):
                self.assertEqual(jobs._get_playback_video_path(job_id, original), original)


if __name__ == "__main__":
    unittest.main()
