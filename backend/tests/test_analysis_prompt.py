import unittest

from backend.services.analysis import _load_prompt_template


class AnalysisPromptTests(unittest.TestCase):
    def test_loads_cinematography_prompt_template(self):
        template = _load_prompt_template("cinematography.md")

        self.assertIn("{SHOTS}", template)
        self.assertIn("{AUDIO}", template)
        self.assertIn("{TRANSCRIPT}", template)


if __name__ == "__main__":
    unittest.main()
