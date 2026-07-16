"""OpenAI-compatible async runner for vision-based shot analysis."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from time import perf_counter
import httpx
from pydantic import BaseModel, Field, ValidationError
from backend.database import get_system_setting
from backend.services.model_calls import record_model_call
from backend.services.model_retry import ModelRequestError, is_retryable_model_error, parse_retry_after
from backend.services.token_usage import try_append_token_usage


@dataclass(frozen=True)
class AnalysisApiConfig:
    model: str
    base_url: str
    api_key: str


class ApiRunnerError(ModelRequestError):
    pass


class AnalysisOutputError(ApiRunnerError):
    def __init__(self, message: str, raw_text: str = ""):
        super().__init__(message)
        self.raw_text = raw_text


class _ShotAnalysis(BaseModel):
    shot_number: int
    duration_sec: float = Field(ge=0)
    analysis: str = Field(min_length=1)
    techniques_to_reference: list[str] = Field(default_factory=list)


class _ShotAnalysisEnvelope(BaseModel):
    shots: list[_ShotAnalysis]


async def get_analysis_api_config() -> AnalysisApiConfig:
    return AnalysisApiConfig(
        model=await get_system_setting("analysis_model", "step-3.7-flash"),
        base_url=(await get_system_setting("analysis_base_url", "https://api.stepfun.com/v1")).rstrip("/"),
        api_key=await get_system_setting("analysis_api_key"),
    )


async def analyze_one_shot(
    shot_number: int,
    content: list[dict],
    queue: asyncio.Queue,
    timeout: int = 600,
    client: httpx.AsyncClient | None = None,
    api_config: AnalysisApiConfig | None = None,
    job_id: str | None = None,
    attempt: int = 1,
) -> dict:
    """Analyze a single shot via the configured API. Returns parsed result dict."""
    config = api_config or await get_analysis_api_config()
    if not config.api_key:
        raise ApiRunnerError(f"API key missing on shot {shot_number}", status_code=401)

    payload = {
        "model": config.model,
        "max_tokens": 8000,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": content}],
    }

    request_started = perf_counter()
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as local_client:
                resp = await _post_chat_completion(local_client, config, payload)
        else:
            resp = await _post_chat_completion(client, config, payload)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        if job_id:
            await asyncio.to_thread(
                record_model_call,
                "job",
                job_id,
                source="analysis",
                model=config.model,
                attempt=attempt,
                outcome="transport_error",
                status_code=None,
                elapsed_ms=round((perf_counter() - request_started) * 1000),
                retryable=is_retryable_model_error(exc),
                error=str(exc),
                shot_number=shot_number,
            )
        raise

    elapsed_ms = round((perf_counter() - request_started) * 1000)

    if resp.status_code != 200:
        retry_after = parse_retry_after(resp.headers.get("retry-after"))
        error = ApiRunnerError(
            f"API error {resp.status_code} on shot {shot_number}: {resp.text[:300]}",
            status_code=resp.status_code,
            retry_after=retry_after,
        )
        if job_id:
            await asyncio.to_thread(
                record_model_call,
                "job",
                job_id,
                source="analysis",
                model=config.model,
                attempt=attempt,
                outcome="http_error",
                status_code=resp.status_code,
                elapsed_ms=elapsed_ms,
                retryable=is_retryable_model_error(error),
                error=str(error),
                shot_number=shot_number,
            )
        raise error

    try:
        data = resp.json()
    except ValueError as exc:
        error = AnalysisOutputError(
            f"Model response is not JSON on shot {shot_number}",
            raw_text=resp.text,
        )
        if job_id:
            await asyncio.to_thread(
                record_model_call,
                "job",
                job_id,
                source="analysis",
                model=config.model,
                attempt=attempt,
                outcome="invalid_response",
                status_code=resp.status_code,
                elapsed_ms=elapsed_ms,
                retryable=False,
                error=str(error),
                shot_number=shot_number,
            )
        raise error from exc
    if job_id:
        await asyncio.to_thread(
            try_append_token_usage,
            job_id,
            source="analysis",
            model=config.model,
            usage=data.get("usage") or {},
            shot_number=shot_number,
        )
    choices = data.get("choices") or []
    if not choices:
        error = AnalysisOutputError(f"API returned empty choices on shot {shot_number}")
        await _record_invalid_analysis_call(job_id, config.model, attempt, shot_number, elapsed_ms, error)
        raise error
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if finish_reason != "stop":
        error = AnalysisOutputError(
            f"Model response incomplete on shot {shot_number}: finish_reason={finish_reason or 'missing'}",
            raw_text=(choice.get("message") or {}).get("content", "") or "",
        )
        await _record_invalid_analysis_call(job_id, config.model, attempt, shot_number, elapsed_ms, error)
        raise error
    message = choice.get("message", {})
    result_text = message.get("content", "")
    reasoning = message.get("reasoning_content", "")

    if reasoning:
        tail = reasoning[-200:] if len(reasoning) > 200 else reasoning
        await queue.put({
            "event": "thinking",
            "data": {"text": tail, "shot_number": shot_number}
        })

    try:
        parsed = _parse_json_output(result_text)
        validated = _ShotAnalysisEnvelope.model_validate(parsed)
    except (ValueError, ValidationError) as exc:
        error = AnalysisOutputError(
            f"Model response structure invalid on shot {shot_number}: {exc}",
            raw_text=result_text,
        )
        await _record_invalid_analysis_call(job_id, config.model, attempt, shot_number, elapsed_ms, error)
        raise error from exc
    if len(validated.shots) != 1:
        error = AnalysisOutputError(
            f"Model must return exactly one shot for shot {shot_number}",
            raw_text=result_text,
        )
        await _record_invalid_analysis_call(job_id, config.model, attempt, shot_number, elapsed_ms, error)
        raise error
    if validated.shots[0].shot_number != shot_number:
        error = AnalysisOutputError(
            f"Model returned shot_number={validated.shots[0].shot_number}, expected {shot_number}",
            raw_text=result_text,
        )
        await _record_invalid_analysis_call(job_id, config.model, attempt, shot_number, elapsed_ms, error)
        raise error
    if job_id:
        await asyncio.to_thread(
            record_model_call,
            "job",
            job_id,
            source="analysis",
            model=config.model,
            attempt=attempt,
            outcome="success",
            status_code=resp.status_code,
            elapsed_ms=elapsed_ms,
            retryable=False,
            usage=data.get("usage") or {},
            shot_number=shot_number,
        )
    return validated.model_dump()


async def _record_invalid_analysis_call(
    job_id: str | None,
    model: str,
    attempt: int,
    shot_number: int,
    elapsed_ms: int,
    error: Exception,
):
    if not job_id:
        return
    await asyncio.to_thread(
        record_model_call,
        "job",
        job_id,
        source="analysis",
        model=model,
        attempt=attempt,
        outcome="invalid_response",
        status_code=200,
        elapsed_ms=elapsed_ms,
        retryable=False,
        error=str(error),
        shot_number=shot_number,
    )


async def _post_chat_completion(client: httpx.AsyncClient, config: AnalysisApiConfig, payload: dict) -> httpx.Response:
    return await client.post(
        f"{config.base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )


def _parse_json_output(result_text: str) -> dict:
    """Extract shot analysis JSON from the model's response text."""
    if not result_text:
        raise ValueError("empty model response")

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

    raise ValueError("response does not contain a valid JSON object")
