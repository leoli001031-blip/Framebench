"""Generate a holistic director's overview after all shots are analyzed."""
import asyncio
import json
from time import perf_counter
import httpx
from backend.database import get_system_setting
from backend.services.model_calls import record_model_call
from backend.services.model_retry import ModelRequestError, is_retryable_model_error, parse_retry_after
from backend.services.token_usage import try_append_token_usage


class OverviewGenerationError(ModelRequestError):
    pass


async def generate_overview(
    shots: list[dict],
    audio_analysis: dict,
    transcript_segments: list[dict],
    job_id: str | None = None,
    attempt: int = 1,
) -> str:
    """Generate a director's overview from all shot analyses.

    shots: list of {shot_number, start_time_sec, end_time_sec, analysis_text, techniques}
    audio_analysis: dict with bpm, energy_curve, brightness, beat_density
    transcript_segments: list of {start_sec, end_sec, text}
    """
    # Build shot summaries
    shot_lines = []
    all_techniques = []
    for s in shots:
        if s.get("analysis_text"):
            shot_lines.append(
                f"镜头{s['shot_number']}({s['start_time_sec']:.0f}-{s['end_time_sec']:.0f}s): "
                f"{s['analysis_text'][:300]}"
            )
            techniques = s.get("techniques") or []
            all_techniques.extend(techniques)

    if not shot_lines:
        return ""

    shot_text = "\n".join(shot_lines)
    unique_techs = list(dict.fromkeys(all_techniques))[:30]
    total_duration = shots[-1]["end_time_sec"] if shots else 0

    # Audio summary
    audio_lines = []
    if audio_analysis and "error" not in audio_analysis:
        bpm = audio_analysis.get("bpm")
        if bpm:
            audio_lines.append(f"BPM: {bpm}")
        energy = audio_analysis.get("quarter_energies", [])
        if energy:
            try:
                avg = sum(float(v) for v in energy) / len(energy)
                audio_lines.append(f"平均能量: {avg:.3f}")
            except (TypeError, ValueError):
                pass
        bd = audio_analysis.get("beat_density")
        if bd:
            try:
                audio_lines.append(f"节拍密度(次/秒): {float(bd):.1f}")
            except (TypeError, ValueError):
                pass

    audio_text = "; ".join(audio_lines) if audio_lines else "无音频数据"

    # Transcript summary
    transcript_text = "无"
    if transcript_segments:
        text = " ".join(s.get("text", "") for s in transcript_segments[:50])
        transcript_text = text[:500] if text else "无"

    prompt = f"""你是一位商业视频导演，请基于以下逐镜头分析和音频数据，写一份全片分析。

## 基本数据
- 总镜头数: {len(shots)}
- 总时长: {total_duration:.0f}秒
- 音频: {audio_text}
- 旁白/台词: {transcript_text}

## 核心技法汇总
{json.dumps(unique_techs, ensure_ascii=False)}

## 逐镜头分析
{shot_text[:8000]}

## 输出要求

从导演视角写一份约 500-700 字的中文分析，作为连贯的段落文字，涵盖以下方面：
1. 视觉风格：色调、构图、光影的整体走向
2. 剪辑节奏：镜头时长的分布规律、节奏变化
3. 运镜模式：主要运镜方式及其叙叙事功能
4. 声画配合：BGM 与画面的节奏关系
5. 结构拆解：开场、发展、高潮、结尾的段落划分

## 语言规则

禁止以下 AI 写作特征词汇：
基底、锚点、母题、织锦、格局、彰显、凸显、标志着、象征着、体现为、充当着、见证了、谱写着、堪称、可谓、此外、至关重要、深入探讨、充满活力、坐落于、令人叹为观止、迷人的、拥有（夸张义）、丰富的（比喻义）、深刻的、展示、体现

禁止以下句式：
- "不仅是……更是……" / "这不只是……而是……"
- "从X到Y，从A到B"的虚假范围句式
- "一方面……另一方面……"
- "可以说" / "毫无疑问" / "显然" / "值得注意的是"
- 以"反映了……""强调了……""突出了……""彰显了……"收尾的句子

禁止以下写法：
- 不要用夸张的比喻和隐喻（"如同""仿佛""就像""XX般的""XX式"）
- 不要用破折号做戏剧性停顿
- 不要拔高到哲学/文化/时代精神
- 不要宣传广告式语言
- 不要强行三段式列举，不要句式机械重复
- 不要引号括起普通词汇进行强调

好的写法：
- 直接陈述观察和判断，少铺垫
- 具体引用镜头号和时间点作为依据
- 数据用原值，不要修饰
- 句子长短交错

直接输出中文分析，不要标题，不要 JSON。"""

    model = await get_system_setting("analysis_model", "step-3.7-flash")
    payload = {
        "model": model,
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}],
    }

    base_url = (await get_system_setting("analysis_base_url", "https://api.stepfun.com/v1")).rstrip("/")
    api_key = await get_system_setting("analysis_api_key")
    request_started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        await _record_overview_call(
            job_id, model, attempt, "transport_error", None,
            round((perf_counter() - request_started) * 1000),
            is_retryable_model_error(exc), error=str(exc),
        )
        raise

    elapsed_ms = round((perf_counter() - request_started) * 1000)

    if resp.status_code != 200:
        error = OverviewGenerationError(
            f"API {resp.status_code}: {resp.text[:200]}",
            status_code=resp.status_code,
            retry_after=parse_retry_after(resp.headers.get("retry-after")),
        )
        await _record_overview_call(
            job_id, model, attempt, "http_error", resp.status_code,
            elapsed_ms, is_retryable_model_error(error), error=str(error),
        )
        raise error

    try:
        data = resp.json()
    except ValueError as exc:
        error = OverviewGenerationError("API returned invalid JSON")
        await _record_overview_call(
            job_id, model, attempt, "invalid_response", resp.status_code,
            elapsed_ms, False, error=str(error),
        )
        raise error from exc
    if job_id:
        await asyncio.to_thread(
            try_append_token_usage,
            job_id,
            source="overview",
            model=model,
            usage=data.get("usage") or {},
        )
    choices = data.get("choices", [])
    if not choices:
        error = OverviewGenerationError("API returned empty choices")
        await _record_overview_call(
            job_id, model, attempt, "invalid_response", resp.status_code,
            elapsed_ms, False, error=str(error),
        )
        raise error
    content = (choices[0].get("message", {}).get("content", "") or "").strip()
    if not content:
        error = OverviewGenerationError("API returned empty overview")
        await _record_overview_call(
            job_id, model, attempt, "invalid_response", resp.status_code,
            elapsed_ms, False, error=str(error),
        )
        raise error
    await _record_overview_call(
        job_id, model, attempt, "success", resp.status_code,
        elapsed_ms, False, usage=data.get("usage") or {},
    )
    return content


async def _record_overview_call(
    job_id: str | None,
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
    if not job_id:
        return
    await asyncio.to_thread(
        record_model_call,
        "job",
        job_id,
        source="overview",
        model=model,
        attempt=attempt,
        outcome=outcome,
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        retryable=retryable,
        usage=usage,
        error=error,
    )
