"""
Moonshot API (Kimi K2.6) async runner for vision-based shot analysis.
Sends HTTP POST with base64-encoded keyframe images + prompt.
"""
import asyncio
import json
import httpx
from backend.database import get_system_setting


async def analyze_one_shot(
    shot_number: int,
    content: list[dict],
    queue: asyncio.Queue,
    timeout: int = 600,
) -> dict:
    """Analyze a single shot via Moonshot API. Returns parsed result dict."""
    payload = {
        "model": await get_system_setting("analysis_model", "kimi-k2.6"),
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": content}],
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{await get_system_setting('analysis_base_url', 'https://api.moonshot.cn/v1')}/chat/completions",
            headers={
                "Authorization": f"Bearer {await get_system_setting('analysis_api_key')}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"API error {resp.status_code} on shot {shot_number}: {resp.text[:300]}"
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
