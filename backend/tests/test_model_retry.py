import unittest

import httpx

from backend.services.model_retry import (
    ModelRequestError,
    is_retryable_model_error,
    model_retry_delay,
    parse_retry_after,
)


class ModelRetryTests(unittest.TestCase):
    def test_transient_http_statuses_are_retryable(self):
        for status_code in (429, 500, 503, 504):
            with self.subTest(status_code=status_code):
                self.assertTrue(is_retryable_model_error(ModelRequestError("temporary", status_code)))

    def test_authentication_error_is_not_retryable(self):
        self.assertFalse(is_retryable_model_error(ModelRequestError("unauthorized", 401)))

    def test_transport_error_is_retryable(self):
        self.assertTrue(is_retryable_model_error(httpx.ConnectError("offline")))

    def test_retry_after_is_parsed_and_capped(self):
        error = ModelRequestError("limited", 429, parse_retry_after("45"))

        self.assertEqual(model_retry_delay(error, 0), 30.0)
        self.assertIsNone(parse_retry_after("tomorrow"))


if __name__ == "__main__":
    unittest.main()
