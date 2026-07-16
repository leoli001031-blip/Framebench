import json
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.services.model_calls import MAX_ERROR_LENGTH, record_model_call


class ModelCallTests(unittest.TestCase):
    def test_records_successful_job_call(self):
        with TemporaryDirectory() as tmpdir:
            recorded = record_model_call(
                "job",
                "job-1",
                source="analysis",
                model="step-3.7-flash",
                attempt=1,
                outcome="success",
                status_code=200,
                elapsed_ms=125,
                retryable=False,
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
                jobs_dir=tmpdir,
            )

            path = os.path.join(tmpdir, "job-1", "model_calls.jsonl")
            with open(path, encoding="utf-8") as ledger:
                record = json.loads(ledger.read())

        self.assertTrue(recorded)
        self.assertEqual(record["scope"], "job")
        self.assertEqual(record["scope_id"], "job-1")
        self.assertEqual(record["source"], "analysis")
        self.assertEqual(record["model"], "step-3.7-flash")
        self.assertEqual(record["attempt"], 1)
        self.assertEqual(record["outcome"], "success")
        self.assertEqual(record["status_code"], 200)
        self.assertEqual(record["elapsed_ms"], 125)
        self.assertFalse(record["retryable"])
        self.assertEqual(record["prompt_tokens"], 10)
        self.assertEqual(record["completion_tokens"], 5)
        self.assertEqual(record["total_tokens"], 15)
        self.assertIsNone(record["error"])
        self.assertNotIn("shot_number", record)

    def test_records_failed_storyboard_call_and_truncates_error(self):
        error = "x" * (MAX_ERROR_LENGTH + 100)
        with TemporaryDirectory() as tmpdir:
            recorded = record_model_call(
                "storyboard",
                "storyboard-1",
                source="image_generation",
                model="step-image-edit-2",
                attempt=3,
                outcome="failure",
                status_code=503,
                elapsed_ms=900,
                retryable=True,
                error=error,
                shot_number=7,
                jobs_dir=tmpdir,
            )

            path = os.path.join(
                tmpdir,
                "storyboards",
                "storyboard-1",
                "model_calls.jsonl",
            )
            with open(path, encoding="utf-8") as ledger:
                record = json.loads(ledger.read())

        self.assertTrue(recorded)
        self.assertEqual(record["scope"], "storyboard")
        self.assertEqual(record["scope_id"], "storyboard-1")
        self.assertEqual(record["outcome"], "failure")
        self.assertEqual(record["shot_number"], 7)
        self.assertEqual(record["prompt_tokens"], 0)
        self.assertEqual(record["completion_tokens"], 0)
        self.assertEqual(record["total_tokens"], 0)
        self.assertEqual(record["error"], error[:MAX_ERROR_LENGTH])

    def test_disk_failure_does_not_escape(self):
        with (
            patch("backend.services.model_calls.os.makedirs", side_effect=OSError("disk full")),
            self.assertLogs("backend.services.model_calls", level="WARNING"),
        ):
            recorded = record_model_call(
                "job",
                "job-1",
                source="overview",
                model="test-model",
                attempt=1,
                outcome="success",
                status_code=200,
                elapsed_ms=10,
                retryable=False,
            )

        self.assertFalse(recorded)


if __name__ == "__main__":
    unittest.main()
