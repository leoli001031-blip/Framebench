"""Generate and persist storyboard shot images."""
from __future__ import annotations

import asyncio
import base64
import binascii
import os
import re
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urlparse

import httpx

from backend.config import JOBS_DIR
from backend.database import get_system_setting
from backend.services.model_calls import record_model_call
from backend.services.model_retry import RETRYABLE_STATUS_CODES, is_retryable_model_error


@dataclass(frozen=True)
class StoryboardImageConfig:
    model: str
    base_url: str
    api_key: str


@dataclass(frozen=True)
class GeneratedStoryboardImage:
    file_path: str | None
    image_url: str


class StoryboardImageError(RuntimeError):
    pass


_IMAGE_PROMPT_MAX_CHARS = 512
_STORYBOARD_PROMPT_PREFIX = (
    "Exactly one uninterrupted 16:9 camera view, filling the canvas as a rough black-and-white "
    "live-action storyboard drawing. Visible construction lines; proportionate human figures "
    "and natural gesture, clear silhouettes, staging, angle, depth and flat gray shading. Scene: "
)
_STORYBOARD_PROMPT_SUFFIX = (
    " Convert every color mentioned into gray values; keep the drawing loose, unfinished and "
    "fully monochrome."
)
_STORYBOARD_NEGATIVE_PROMPT = (
    "photograph, photorealistic, polished illustration, finished artwork, concept art, digital "
    "painting, 3D render, realistic texture, color, colored accent, orange, yellow, red, blue, "
    "green, cartoon, anime, manga, chibi, comic character, mascot, storyboard sheet, panel grid, "
    "collage, split screen, inset image, multiple panels, text, caption, logo, frame number, "
    "arrow, border"
)
_LEGACY_RENDER_TERMS = re.compile(
    r"cinematic\s+concept\s+art|hyper[-\s]?realistic|photorealistic|high\s+fidelity|"
    r"(?<!\w)8k(?!\w)|--ar\s+16:9",
    re.IGNORECASE,
)
_LEGACY_COLOR_TERMS = re.compile(
    r"\b(?:warm|cool|cold|red|orange|yellow|green|blue|purple|pink|gold(?:en)?|silver|"
    r"teal|cyan|magenta|brown|beige|vivid|saturated|colorful)\b(?:-(?=\w))?",
    re.IGNORECASE,
)


def _build_storyboard_image_prompt(scene_prompt: str) -> str:
    scene = _LEGACY_RENDER_TERMS.sub("", scene_prompt.strip())
    scene = _LEGACY_COLOR_TERMS.sub("", scene)
    scene = re.sub(r"\s*,\s*,+", ", ", scene)
    scene = re.sub(r"\s{2,}", " ", scene).strip(" ,.;-")
    if not scene:
        scene = "Clear subject blocking and camera composition"

    scene_limit = _IMAGE_PROMPT_MAX_CHARS - len(_STORYBOARD_PROMPT_PREFIX) - len(_STORYBOARD_PROMPT_SUFFIX) - 1
    scene = scene[:scene_limit].rstrip(" ,.;-")
    return f"{_STORYBOARD_PROMPT_PREFIX}{scene}.{_STORYBOARD_PROMPT_SUFFIX}"


async def get_storyboard_image_config() -> StoryboardImageConfig:
    return StoryboardImageConfig(
        model=await get_system_setting("image_model", "step-image-edit-2"),
        base_url=(await get_system_setting("image_base_url", "https://api.stepfun.com/v1")).rstrip("/"),
        api_key=await get_system_setting("image_api_key"),
    )


async def generate_storyboard_shot_image(
    *,
    storyboard_id: str,
    shot_number: int,
    prompt: str,
    config: StoryboardImageConfig | None = None,
    jobs_dir: str = JOBS_DIR,
    client: httpx.AsyncClient | None = None,
) -> GeneratedStoryboardImage | None:
    image_config = config or await get_storyboard_image_config()
    if not image_config.api_key:
        return None

    if not prompt.strip():
        raise StoryboardImageError(f"shot {shot_number}: image prompt is empty")

    payload = _image_generation_payload(image_config, prompt)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=180)
    request_started = perf_counter()
    try:
        try:
            resp = await http_client.post(
                _image_generation_endpoint(image_config.base_url),
                headers={
                    "Authorization": f"Bearer {image_config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            await _record_image_call(
                storyboard_id, shot_number, image_config.model, "transport_error", None,
                round((perf_counter() - request_started) * 1000),
                is_retryable_model_error(exc), jobs_dir, error=str(exc),
            )
            raise
    finally:
        if owns_client:
            await http_client.aclose()

    elapsed_ms = round((perf_counter() - request_started) * 1000)
    if resp.status_code != 200:
        error = StoryboardImageError(f"image API error {resp.status_code}: {resp.text[:300]}")
        await _record_image_call(
            storyboard_id, shot_number, image_config.model, "http_error", resp.status_code,
            elapsed_ms, resp.status_code in RETRYABLE_STATUS_CODES, jobs_dir, error=str(error),
        )
        raise error

    try:
        data = resp.json()
    except ValueError as exc:
        error = StoryboardImageError("image API returned invalid JSON")
        await _record_image_call(
            storyboard_id, shot_number, image_config.model, "invalid_response", resp.status_code,
            elapsed_ms, False, jobs_dir, error=str(error),
        )
        raise error from exc
    image_item = (data.get("data") or [{}])[0]
    b64_text = image_item.get("b64_json")
    if not b64_text:
        remote_url = image_item.get("url")
        if remote_url:
            await _record_image_call(
                storyboard_id, shot_number, image_config.model, "success", resp.status_code,
                elapsed_ms, False, jobs_dir, usage=data.get("usage") or {},
            )
            return GeneratedStoryboardImage(file_path=None, image_url=remote_url)
        error = StoryboardImageError("image API returned no image data")
        await _record_image_call(
            storyboard_id, shot_number, image_config.model, "invalid_response", resp.status_code,
            elapsed_ms, False, jobs_dir, usage=data.get("usage") or {}, error=str(error),
        )
        raise error

    try:
        image_bytes = base64.b64decode(b64_text, validate=True)
    except (binascii.Error, ValueError) as exc:
        error = StoryboardImageError("image API returned invalid base64 data")
        await _record_image_call(
            storyboard_id, shot_number, image_config.model, "invalid_response", resp.status_code,
            elapsed_ms, False, jobs_dir, usage=data.get("usage") or {}, error=str(error),
        )
        raise error from exc

    await _record_image_call(
        storyboard_id, shot_number, image_config.model, "success", resp.status_code,
        elapsed_ms, False, jobs_dir, usage=data.get("usage") or {},
    )

    ext = _image_extension(image_bytes)
    shot_filename = f"shot_{shot_number:04d}.{ext}"
    output_dir = os.path.join(jobs_dir, "storyboards", storyboard_id)
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, shot_filename)
    with open(file_path, "wb") as f:
        f.write(image_bytes)

    return GeneratedStoryboardImage(
        file_path=file_path,
        image_url=f"/api/frames/storyboards/{storyboard_id}/{shot_filename}",
    )


def _image_generation_payload(config: StoryboardImageConfig, prompt: str) -> dict:
    payload = {
        "model": config.model,
        "prompt": _build_storyboard_image_prompt(prompt),
        "n": 1,
    }
    hostname = (urlparse(config.base_url).hostname or "").lower()
    if hostname == "stepfun.com" or hostname.endswith(".stepfun.com"):
        payload.update({
            "size": "768x1360",
            "response_format": "b64_json",
            "steps": 8,
            "cfg_scale": 3.0,
            "negative_prompt": _STORYBOARD_NEGATIVE_PROMPT,
            "text_mode": False,
        })
    return payload


async def _record_image_call(
    storyboard_id: str,
    shot_number: int,
    model: str,
    outcome: str,
    status_code: int | None,
    elapsed_ms: int,
    retryable: bool,
    jobs_dir: str,
    *,
    usage: dict | None = None,
    error: str | None = None,
):
    await asyncio.to_thread(
        record_model_call,
        "storyboard",
        storyboard_id,
        source="storyboard_image",
        model=model,
        attempt=1,
        outcome=outcome,
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        retryable=retryable,
        usage=usage,
        error=error,
        shot_number=shot_number,
        jobs_dir=jobs_dir,
    )


def _image_generation_endpoint(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/images/generations"):
        return trimmed
    return f"{trimmed}/images/generations"


def _image_extension(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\xff\xd8"):
        return "jpg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "webp"
    return "png"
