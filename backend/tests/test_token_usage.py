import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.services.token_usage import append_token_usage, summarize_token_usage, try_append_token_usage


class TokenUsageTests(unittest.TestCase):
    def test_summarize_missing_usage_returns_empty_source_list(self):
        with TemporaryDirectory() as tmpdir:
            summary = summarize_token_usage("job-1", jobs_dir=tmpdir)

        self.assertFalse(summary["exists"])
        self.assertEqual(summary["by_source"], [])

    def test_append_and_summarize_token_usage(self):
        with TemporaryDirectory() as tmpdir:
            append_token_usage(
                "job-1",
                source="analysis",
                model="step-3.7-flash",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                shot_number=1,
                jobs_dir=tmpdir,
            )
            append_token_usage(
                "job-1",
                source="overview",
                model="step-3.7-flash",
                usage={"prompt_tokens": 7, "completion_tokens": 3},
                jobs_dir=tmpdir,
            )

            summary = summarize_token_usage("job-1", jobs_dir=tmpdir)

        self.assertTrue(summary["exists"])
        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["prompt_tokens"], 17)
        self.assertEqual(summary["completion_tokens"], 8)
        self.assertEqual(summary["total_tokens"], 25)
        self.assertEqual({row["source"] for row in summary["by_source"]}, {"analysis", "overview"})

    def test_append_ignores_empty_usage(self):
        with TemporaryDirectory() as tmpdir:
            append_token_usage(
                "job-1",
                source="analysis",
                model="step-3.7-flash",
                usage={},
                jobs_dir=tmpdir,
            )

            self.assertFalse(os.path.exists(os.path.join(tmpdir, "job-1", "token_usage.jsonl")))

    def test_best_effort_append_does_not_raise_on_accounting_failure(self):
        with (
            patch("backend.services.token_usage.append_token_usage", side_effect=OSError("disk full")),
            self.assertLogs("backend.services.token_usage", level="WARNING"),
        ):
            self.assertFalse(try_append_token_usage(
                "job-1",
                source="analysis",
                model="test-model",
                usage={"total_tokens": 10},
            ))


if __name__ == "__main__":
    unittest.main()
