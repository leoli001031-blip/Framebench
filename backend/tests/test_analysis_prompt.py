import unittest

from backend.services.analysis import _format_transcript_for_shot, _load_prompt_template


class AnalysisPromptTests(unittest.TestCase):
    def test_loads_cinematography_prompt_template(self):
        template = _load_prompt_template("cinematography.md")

        self.assertIn("{SHOTS}", template)
        self.assertIn("{AUDIO}", template)
        self.assertIn("{TRANSCRIPT}", template)

    def test_transcript_context_is_scoped_to_current_shot(self):
        transcript = [
            {"start": 0.0, "end": 1.0, "text": "片头"},
            {"start": 8.5, "end": 9.5, "text": "临近对白"},
            {"start": 10.2, "end": 11.2, "text": "镜内对白"},
            {"start": 20.0, "end": 21.0, "text": "远处对白"},
        ]

        text = _format_transcript_for_shot(transcript, 10.0, 12.0)

        self.assertIn("临近对白", text)
        self.assertIn("镜内对白", text)
        self.assertNotIn("片头", text)
        self.assertNotIn("远处对白", text)

    def test_transcript_context_has_explicit_empty_state(self):
        text = _format_transcript_for_shot([], 10.0, 12.0)

        self.assertEqual(text, "（当前镜头无对白或旁白）")


if __name__ == "__main__":
    unittest.main()
