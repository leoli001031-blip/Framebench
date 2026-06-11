"""
Moonshot API (Kimi K2.6) async runner for vision-based shot analysis.
Sends HTTP POST with base64-encoded keyframe images + prompt.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import httpx
from backend.database import get_system_setting


@dataclass(frozen=True)
class AnalysisApiConfig:
    model: str
    base_url: str
    api_key: str


class ApiRunnerError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


async def get_analysis_api_config() -> AnalysisApiConfig:
    return AnalysisApiConfig(
        model=await get_system_setting("analysis_model", "kimi-k2.6"),
        base_url=(await get_system_setting("analysis_base_url", "https://api.moonshot.cn/v1")).rstrip("/"),
        api_key=await get_system_setting("analysis_api_key"),
    )


async def analyze_one_shot(
    shot_number: int,
    content: list[dict],
    queue: asyncio.Queue,
    timeout: int = 600,
    client: httpx.AsyncClient | None = None,
    api_config: AnalysisApiConfig | None = None,
) -> dict:
    """Analyze a single shot via Moonshot API. Returns parsed result dict."""
    config = api_config or await get_analysis_api_config()
    if not config.api_key:
        raise ApiRunnerError(f"API key missing on shot {shot_number}", status_code=401)

    payload = {
        "model": config.model,
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": content}],
    }

    if client is None:
        async with httpx.AsyncClient(timeout=timeout) as local_client:
            resp = await _post_chat_completion(local_client, config, payload)
    else:
        resp = await _post_chat_completion(client, config, payload)

    if resp.status_code != 200:
        retry_after = _parse_retry_after(resp.headers.get("retry-after"))
        raise ApiRunnerError(
            f"API error {resp.status_code} on shot {shot_number}: {resp.text[:300]}",
            status_code=resp.status_code,
            retry_after=retry_after,
        )

    data = resp.json()
    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    result_text = message.get("content", "")
    reasoning = message.get("reasoning_content", "")

    if reasoning:
        tail = reasoning[-200:] if len(reasoning) > 200 else reasoning
        await queue.put({
            "event": "thinking",
            "data": {"text": tail, "shot_number": shot_number}
        })

    parsed = _parse_json_output(result_text)
    # Inject shot_number if the model didn't include it
    for s in parsed.get("shots", []):
        if "shot_number" not in s:
            s["shot_number"] = shot_number
    return parsed


async def _post_chat_completion(client: httpx.AsyncClient, config: AnalysisApiConfig, payload: dict) -> httpx.Response:
    return await client.post(
        f"{config.base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _parse_json_output(result_text: str) -> dict:
    """Extract shot analysis JSON from the model's response text."""
    if not result_text:
        return {"shots": []}

    try:
        if "```json" in result_text:
            start = result_text.index("```json") + 7
            end = result_text.index("```", start)
            return json.loads(result_text[start:end].strip())
    except (ValueError, json.JSONDecodeError):
        pass

    try:
        start = result_text.index("{")
        end = result_text.rindex("}") + 1
        return json.loads(result_text[start:end])
    except (ValueError, json.JSONDecodeError):
        pass

    return {"shots": [], "raw": result_text}
