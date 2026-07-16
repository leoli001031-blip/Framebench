import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from backend.services.frame_analyzer import analyze_frame


class FrameAnalyzerTests(unittest.TestCase):
    def test_analyze_frame_handles_max_uint8_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = os.path.join(tmpdir, "white.png")
            Image.new("RGB", (4, 4), (255, 255, 255)).save(image_path)

            result = analyze_frame(image_path)

        self.assertIsNotNone(result)
        self.assertEqual(result["dominant_colors"][0]["hex"], "#ffffff")

    def test_analyze_frame_downscales_working_array_and_preserves_resolution(self):
        seen_shape = None

        def capture_shape(arr, n_colors=4):
            nonlocal seen_shape
            seen_shape = arr.shape
            return []

        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = os.path.join(tmpdir, "large.jpg")
            Image.new("RGB", (2000, 1200), (120, 80, 40)).save(image_path)
            with patch("backend.services.frame_analyzer._extract_dominant_colors", side_effect=capture_shape):
                result = analyze_frame(image_path)

        self.assertIsNotNone(result)
        self.assertEqual(result["resolution"], "2000x1200")
        self.assertIsNotNone(seen_shape)
        self.assertLessEqual(max(seen_shape[:2]), 1024)


if __name__ == "__main__":
    unittest.main()
