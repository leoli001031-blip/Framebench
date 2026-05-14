import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
