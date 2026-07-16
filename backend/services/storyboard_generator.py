"""Generate new storyboard based on reference video analyses and user brief."""
import asyncio
import json
from time import perf_counter
import httpx
from pydantic import ValidationError
from typing import Callable, Awaitable, Optional
from backend.database import get_system_setting
from backend.schemas import StoryboardResponse
from backend.services.model_calls import record_model_call
from backend.services.model_retry import (
    ModelRequestError,
    is_retryable_model_error,
    model_retry_delay,
    parse_retry_after,
)


def _build_reference_context(references: list[dict], max_chars: int = 18000) -> tuple[str, int]:
    if not references or max_chars <= 0:
        return "", 0

    source_count = len(references)
    summary_budget = int(max_chars * 0.45)
    per_source_budget = max(120, summary_budget // source_count)
    source_sections: list[str] = []
    shot_groups: list[list[dict]] = []

    for reference in references:
        header = (
            f"### 来源档案: {str(reference.get('filename', '未命名'))[:100]} "
            f"(类别: {str(reference.get('category') or '通用')[:30]})"
        )
        overview = str(reference.get("overview_text", "")).strip()
        overview_limit = max(0, per_source_budget - len(header) - len("\n核心调性: "))
        section = header
        if overview and overview_limit:
            section += f"\n核心调性: {overview[:overview_limit]}"
        source_sections.append(section)

        shots = sorted(
            list(reference.get("shots", [])),
            key=lambda shot: int(shot.get("shot_number", 0)),
        )
        if len(shots) > 8 and not reference.get("shots_are_selected"):
            last_index = len(shots) - 1
            indices = [round(index * last_index / 7) for index in range(8)]
            shots = [shots[index] for index in indices]
        shot_groups.append(shots)

    parts = source_sections[:]
    used_shots = 0
    selected_shots = [
        shot
        for shots in shot_groups
        for shot in shots
        if "selection_order" in shot
    ]
    if selected_shots:
        shot_sequence = sorted(selected_shots, key=lambda shot: int(shot["selection_order"]))
    else:
        shot_sequence = []
        max_group_size = max((len(group) for group in shot_groups), default=0)
        for shot_index in range(max_group_size):
            shot_sequence.extend(
                shots[shot_index]
                for shots in shot_groups
                if shot_index < len(shots)
            )

    for shot in shot_sequence:
        analysis = str(shot.get("analysis_text", "")).strip()
        if not analysis:
            continue
        source = str(shot.get("source_filename", "")).strip()
        source_note = f" [来源: {source[:80]}]" if source else ""
        line = (
            f"- 镜头 {shot['shot_number']}({shot.get('duration_sec', '?')}s){source_note}: "
            f"{analysis[:420]}"
        )
        candidate_length = len("\n".join(parts)) + 1 + len(line)
        if candidate_length > max_chars:
            break
        parts.append(line)
        used_shots += 1

    return "\n".join(parts)[:max_chars], used_shots


async def generate_storyboard(
    brief: str,
    references: list[dict],
    target_duration: int = None,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    model_call_id: str | None = None,
) -> dict:
    """Generate a new storyboard from brief and reference analyses."""
    async def progress(msg: str):
        if progress_callback:
            await progress_callback(msg)

    await progress("正在收集参考片分析数据…")

    # Build a bounded reference summary instead of sending every stored shot.
    all_techniques = []
    for r in references:
        all_techniques.extend(r.get("all_techniques", []))

    ref_text, used_shots = _build_reference_context(references)
    unique_techniques = list(dict.fromkeys(all_techniques))[:30]

    total_shots = sum(len(r.get("shots", [])) for r in references)
    await progress(f"已收集 {len(references)} 个参考片，从 {total_shots} 个镜头提取 {used_shots} 个代表镜头")

    duration_hint = f"预期时长: 约 {target_duration} 秒。" if target_duration else "时长不限。"
    
    prompt = f"""你是一位享誉国际的商业短片导演与视觉叙事专家。
请基于提供的【参考片档案】中的风格与技法，为【新创作需求】设计一份具备电影感、高审美层级的分镜方案。

## 1. 创作上下文
### 参考片风格提取
{ref_text}

### 可选技法库
{json.dumps(unique_techniques, ensure_ascii=False)}

### 新创作需求 (Brief)
{brief[:2000]}
{duration_hint}

## 2. 分镜设计核心准则

### 节奏与叙事逻辑
拒绝套路化的平铺直叙。请基于创作需求的核心情感，自主设计一套独特的节奏曲线。
- 它可以是跳跃的、诗意的、或者是蒙太奇式的非线性组合。
- 重点在于如何通过画面间的情绪张力，而非传统的“起承转合”来调动观众。
- 允许出现意外的视觉转折或节奏突变，以增加作品的灵气与现代感。

### 画面描述：具体而克制
不要使用模糊的辞藻。请在保持简短的前提下，精准捕捉画面的“质感”：
- 空间感：明确主体在空间中的具体位置（如：斜切入画、占据画面右侧 1/3）。
- 质感细节：点出一处具体的材质或物理细节（如：清透的玻璃质地、略带锈迹的金属边缘、或是柔和的丝绸纹理）。
- 确切动作：描述一个极具代表性的瞬间（如：指尖划过桌面的刹那、或者是烟雾在逆光下消散的状态）。
- 每一镜字数控制在 50-80 字左右，追求“一语中的”。

### 技法深度迁移 (reference_from)
不要机械模仿，要"神似"。请在此字段注明你从参考片中汲取了什么灵感（如：快节奏跳剪逻辑、微距细节特写、或者是某种特定的光影过渡方式）。

### 生图提示词 (image_prompt)
为每一镜撰写一段简洁的英文“画面内容提示词”，用于生成制作分镜草图，而不是电影成片或概念图。系统会统一添加分镜绘制风格，因此这里仅描述这一格“拍什么、怎么构图”。要求：
- 包含：Shot size, camera angle, subject position and action, foreground / middle ground / background, key light direction and tonal emphasis.
- 描述一个清晰的决定性瞬间，保持主体轮廓和空间关系明确，控制在 260 个英文字符以内。
- 不要加入 photorealistic、concept art、3D render、8K、high fidelity 等渲染质量词，也不要加入 `--ar` 等模型参数。
- 不要要求生成文字、字幕、Logo、镜头编号、箭头、边框或多格分镜；这些信息由界面在图片外展示。

## 3. 输出格式 (只输出合法 JSON)

```json
{{
  "title": "脚本标题 (需具备电影感)",
  "full_notes": "整体视觉策略说明：涵盖叙事逻辑、光影策略及情绪转折。",
  "total_duration_sec": 0,
  "shots": [
    {{
      "shot_number": 1,
      "duration_sec": 1.5,
      "description": "镜头画面描述：包含景别、主体动作、具体的光影与色彩处理。",
      "camera_movement": "固定 / 推 / 拉 / 摇 / 移 / 跟 / 升 / 降 / 手持",
      "bgm_note": "此处的声画配合建议（如：切分音对位、环境音淡入）。",
      "reference_from": "灵感来源：对参考片技法的具体演绎说明。",
      "image_prompt": "Wide low-angle view of a runner entering from frame left, crowd silhouettes in foreground, finish line centered in the background, hard side light defining the runner's outline"
    }}
  ]
}}
```"""

    payload = {
        "model": await get_system_setting("storyboard_model", "step-3.7-flash"),
        "max_tokens": 16000,
        "temperature": 0.7,  # Add some creativity
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "你是一位专业的电影导演，擅长将参考片的视觉灵魂转化为全新的创意脚本。"},
            {"role": "user", "content": prompt}
        ],
    }

    await progress("正在调用 AI 模型生成分镜方案（预计 1-3 分钟）…")

    base_url = (await get_system_setting("storyboard_base_url", "https://api.stepfun.com/v1")).rstrip("/")
    api_key = await get_system_setting("storyboard_api_key")
    successful_attempt = 1
    elapsed_ms = 0
    async with httpx.AsyncClient(timeout=600) as client:
        for attempt in range(3):
            request_started = perf_counter()
            try:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if resp.status_code != 200:
                    raise ModelRequestError(
                        f"API error {resp.status_code}: {resp.text[:300]}",
                        status_code=resp.status_code,
                        retry_after=parse_retry_after(resp.headers.get("retry-after")),
                    )
                successful_attempt = attempt + 1
                elapsed_ms = round((perf_counter() - request_started) * 1000)
                break
            except (ModelRequestError, httpx.TimeoutException, httpx.TransportError) as exc:
                status_code = getattr(exc, "status_code", None)
                await _record_storyboard_call(
                    model_call_id,
                    payload["model"],
                    attempt + 1,
                    "http_error" if status_code is not None else "transport_error",
                    status_code,
                    round((perf_counter() - request_started) * 1000),
                    is_retryable_model_error(exc),
                    error=str(exc),
                )
                if attempt >= 2 or not is_retryable_model_error(exc):
                    raise
                await asyncio.sleep(model_retry_delay(exc, attempt))

    try:
        data = resp.json()
    except ValueError as exc:
        error = RuntimeError("分镜 API 返回了无效 JSON")
        await _record_storyboard_call(
            model_call_id, payload["model"], successful_attempt, "invalid_response",
            resp.status_code, elapsed_ms, False, error=str(error),
        )
        raise error from exc
    choices = data.get("choices", [])
    if not choices:
        error = RuntimeError("API returned empty choices array")
        await _record_storyboard_call(
            model_call_id, payload["model"], successful_attempt, "invalid_response",
            resp.status_code, elapsed_ms, False, usage=data.get("usage") or {}, error=str(error),
        )
        raise error
    finish_reason = choices[0].get("finish_reason")
    if finish_reason != "stop":
        error = RuntimeError(f"分镜生成未完整结束: finish_reason={finish_reason or 'missing'}")
        await _record_storyboard_call(
            model_call_id, payload["model"], successful_attempt, "invalid_response",
            resp.status_code, elapsed_ms, False, usage=data.get("usage") or {}, error=str(error),
        )
        raise error

    result_text = choices[0].get("message", {}).get("content", "") or ""

    await progress("AI 生成完成，正在解析结果…")

    # Parse JSON from response
    parsed = None
    try:
        # Standard JSON block parsing
        if "```json" in result_text:
            start = result_text.index("```json") + 7
            end = result_text.index("```", start)
            parsed = json.loads(result_text[start:end].strip())
    except (ValueError, json.JSONDecodeError):
        pass

    if parsed is None:
        # Fallback to direct object parsing
        try:
            start = result_text.index("{")
            end = result_text.rindex("}") + 1
            parsed = json.loads(result_text[start:end])
        except (ValueError, json.JSONDecodeError):
            pass

    if parsed is None:
        error = RuntimeError(f"无法解析分镜数据: {result_text[:500]}")
        await _record_storyboard_call(
            model_call_id, payload["model"], successful_attempt, "invalid_response",
            resp.status_code, elapsed_ms, False, usage=data.get("usage") or {}, error=str(error),
        )
        raise error

    if "total_duration_sec" not in parsed or parsed["total_duration_sec"] == 0:
        parsed["total_duration_sec"] = round(sum(
            s.get("duration_sec", 0) for s in parsed.get("shots", [])
        ), 1)

    try:
        validated = StoryboardResponse.model_validate(parsed)
    except ValidationError as exc:
        error = RuntimeError(f"分镜数据结构不完整: {exc}")
        await _record_storyboard_call(
            model_call_id, payload["model"], successful_attempt, "invalid_response",
            resp.status_code, elapsed_ms, False, usage=data.get("usage") or {}, error=str(error),
        )
        raise error from exc

    shot_numbers = [shot.shot_number for shot in validated.shots]
    if shot_numbers != list(range(1, len(shot_numbers) + 1)):
        error = RuntimeError("分镜数据结构异常: 镜头号必须从 1 连续递增")
        await _record_storyboard_call(
            model_call_id, payload["model"], successful_attempt, "invalid_response",
            resp.status_code, elapsed_ms, False, usage=data.get("usage") or {}, error=str(error),
        )
        raise error
    await _record_storyboard_call(
        model_call_id, payload["model"], successful_attempt, "success",
        resp.status_code, elapsed_ms, False, usage=data.get("usage") or {},
    )
    return validated.model_dump()


async def _record_storyboard_call(
    scope_id: str | None,
    model: str,
    attempt: int,
    outcome: str,
    status_code: int | None,
    elapsed_ms: int,
    retryable: bool,
    *,
    usage: dict | None = None,
    error: str | None = None,
):
    if not scope_id:
        return
    await asyncio.to_thread(
        record_model_call,
        "storyboard",
        scope_id,
        source="storyboard_text",
        model=model,
        attempt=attempt,
        outcome=outcome,
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        retryable=retryable,
        usage=usage,
        error=error,
    )
