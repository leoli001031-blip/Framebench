import logging
import unittest

from backend.main import _SecretRedactionFilter


class MainLoggingTests(unittest.TestCase):
    def test_secret_redaction_filter_redacts_record_message_and_args(self):
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="GET /api/jobs?token=local-secret HTTP/1.1",
            args=("Authorization: Bearer sk-test-secret",),
            exc_info=None,
        )

        self.assertTrue(_SecretRedactionFilter().filter(record))

        self.assertIn("token=[REDACTED]", record.msg)
        self.assertNotIn("local-secret", record.msg)
        self.assertEqual(record.args, ("Authorization: Bearer [REDACTED]",))


if __name__ == "__main__":
    unittest.main()
