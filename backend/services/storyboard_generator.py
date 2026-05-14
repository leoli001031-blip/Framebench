"""Generate new storyboard based on reference video analyses and user brief."""
import json
import httpx
from typing import Callable, Awaitable, Optional
from backend.database import get_system_setting


async def generate_storyboard(
    brief: str,
    references: list[dict],
    target_duration: int = None,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    """Generate a new storyboard from brief and reference analyses."""
    async def progress(msg: str):
        if progress_callback:
            await progress_callback(msg)

    await progress("正在收集参考片分析数据…")

    # Build a more structured reference summary
    ref_parts = []
    all_techniques = []
    for r in references:
        ref_parts.append(f"### 来源档案: {r['filename']} (类别: {r.get('category', '通用')})")
        overview = r.get("overview_text", "")
        if overview:
            ref_parts.append(f"核心调性: {overview}")
        
        shots = r.get("shots", [])
        # Send all shots' analysis for richer reference
        for s in shots:
            ref_parts.append(
                f"- 镜头 {s['shot_number']}({s.get('duration_sec', '?')}s): {s['analysis_text']}"
            )
        all_techniques.extend(r.get("all_techniques", []))

    ref_text = "\n".join(ref_parts)
    unique_techniques = list(dict.fromkeys(all_techniques))[:30]

    total_shots = sum(len(r.get("shots", [])) for r in references)
    await progress(f"已收集 {len(references)} 个参考片，共 {total_shots} 个镜头")

    duration_hint = f"预期时长: 约 {target_duration} 秒。" if target_duration else "时长不限。"
    
    prompt = f"""你是一位享誉国际的商业短片导演与视觉叙事专家。
请基于提供的【参考片档案】中的风格与技法，为【新创作需求】设计一份具备电影感、高审美层级的分镜方案。

## 1. 创作上下文
### 参考片风格提取
{ref_text[:50000]}

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
为每一镜撰写一段极高质量的英文提示词，用于 Midjourney 生成。要求：
- 风格：Cinematic concept art, photorealistic, shot on 35mm lens, high fidelity.
- 包含：Camera angle, lighting condition, primary subject action, color palette, texture details.
- 比例：--ar 16:9

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
      "image_prompt": "Midjourney prompt: [Subject + Action], [Lighting], [Composition], [Color Palette], [Camera Lens], Cinematic Art, 8K --ar 16:9"
    }}
  ]
}}
```"""

    payload = {
        "model": await get_system_setting("storyboard_model", "kimi-k2.6"),
        "max_tokens": 16000,
        "temperature": 0.7,  # Add some creativity
        "messages": [
            {"role": "system", "content": "你是一位专业的电影导演，擅长将参考片的视觉灵魂转化为全新的创意脚本。"},
            {"role": "user", "content": prompt}
        ],
    }

    await progress("正在调用 AI 模型生成分镜方案（预计 1-3 分钟）…")

    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(
            f"{await get_system_setting('storyboard_base_url', 'https://api.moonshot.cn/v1')}/chat/completions",
            headers={
                "Authorization": f"Bearer {await get_system_setting('storyboard_api_key')}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"API error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("API returned empty choices array")
    
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
        raise RuntimeError(f"无法解析分镜数据: {result_text[:500]}")

    if "total_duration_sec" not in parsed or parsed["total_duration_sec"] == 0:
        parsed["total_duration_sec"] = round(sum(
            s.get("duration_sec", 0) for s in parsed.get("shots", [])
        ), 1)

    return parsed
