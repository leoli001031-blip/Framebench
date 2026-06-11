import asyncio
import base64
import json
import os
import tempfile
import unittest
from io import BytesIO

import httpx
from PIL import Image

from backend.services.analysis import _encode_keyframe_for_analysis
from backend.services.api_runner import AnalysisApiConfig, analyze_one_shot


class AnalysisImageEncodingTests(unittest.TestCase):
    def test_keyframe_encoding_downscales_large_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "frame.jpg")
            Image.new("RGB", (2000, 1200), color=(120, 80, 40)).save(path, "JPEG")

            encoded = _encode_keyframe_for_analysis(path)
            decoded = Image.open(BytesIO(base64.b64decode(encoded)))

        self.assertLessEqual(max(decoded.size), 1024)


class ApiRunnerReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_one_shot_uses_passed_client_and_config(self):
        seen = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            payload = json.loads(request.content.decode())
            seen["model"] = payload["model"]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"shots": [{"analysis": "ok"}]}),
                                "reasoning_content": "thinking",
                            }
                        }
                    ]
                },
            )

        queue: asyncio.Queue = asyncio.Queue()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await analyze_one_shot(
                7,
                [{"type": "text", "text": "prompt"}],
                queue,
                client=client,
                api_config=AnalysisApiConfig(
                    model="test-model",
                    base_url="https://example.test/v1",
                    api_key="test-key",
                ),
            )

        self.assertEqual(result["shots"][0]["shot_number"], 7)
        self.assertEqual(seen["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(seen["auth"], "Bearer test-key")
        self.assertEqual(seen["model"], "test-model")


if __name__ == "__main__":
    unittest.main()
