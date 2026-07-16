import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend.services.overview_generator import OverviewGenerationError, generate_overview


class _FakeClient:
    def __init__(self, response: httpx.Response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, *args, **kwargs):
        return self.response


class OverviewGeneratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_error_is_raised_instead_of_returned_as_overview_text(self):
        response = httpx.Response(429, text="rate limited")
        with (
            patch("backend.services.overview_generator.httpx.AsyncClient", return_value=_FakeClient(response)),
            patch(
                "backend.services.overview_generator.get_system_setting",
                new=AsyncMock(side_effect=lambda key, default="": default or "test-key"),
            ),
        ):
            with self.assertRaisesRegex(OverviewGenerationError, "API 429"):
                await generate_overview(
                    [{
                        "shot_number": 1,
                        "start_time_sec": 0,
                        "end_time_sec": 1,
                        "analysis_text": "analysis",
                        "techniques": [],
                    }],
                    {},
                    [],
                )


if __name__ == "__main__":
    unittest.main()
