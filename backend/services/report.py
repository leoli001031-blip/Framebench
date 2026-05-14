import asyncio
import json
import os
from sqlalchemy import select
from backend.config import JOBS_DIR
from backend.database import AsyncSessionLocal
from backend.models import Job, Shot, Dimension


async def build_report(job_id: str) -> str:
    """Generate Markdown report from DB data."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            return ""

        lines = []
        lines.append(f"# Framebench 拉片报告\n")
        lines.append(f"**影片**: {job.filename}  \n")
        lines.append(f"**分析时间**: {job.created_at.strftime('%Y-%m-%d %H:%M')}  \n")
        lines.append(f"**总镜头数**: {job.total_shots}  \n")
        if job.duration_sec:
            lines.append(f"**时长**: {job.duration_sec:.0f}s  \n")
        lines.append("\n---\n\n")

        lines.append("## 影片概览\n\n")

        # Aggregate stats
        shot_result = await db.execute(
            select(Shot).where(Shot.job_id == job_id).order_by(Shot.shot_number)
        )
        shots = shot_result.scalars().all()

        # Collect dimension stats
        dim_counts = {}
        all_dims = ["shot_scale", "composition", "camera_angle", "lens", "lighting_type", "lighting_condition", "camera_movement", "color"]

        dim_result = await db.execute(
            select(Dimension).join(Shot).where(Shot.job_id == job_id)
        )
        dimensions = dim_result.scalars().all()

        for dim in dimensions:
            if dim.dimension_name not in dim_counts:
                dim_counts[dim.dimension_name] = {"labels": {}}
            label = dim.label or "unknown"
            dim_counts[dim.dimension_name]["labels"][label] = dim_counts[dim.dimension_name]["labels"].get(label, 0) + 1

        for dim_name in all_dims:
            if dim_name in dim_counts:
                top = sorted(dim_counts[dim_name]["labels"].items(), key=lambda x: -x[1])[:3]
                dim_label_map = {
                    "shot_scale": "景别", "composition": "构图", "camera_angle": "角度",
                    "lens": "焦段", "lighting_type": "灯光类型", "lighting_condition": "灯光条件",
                    "camera_movement": "运镜", "color": "色彩"
                }
                label = dim_label_map.get(dim_name, dim_name)
                top_str = " / ".join(f"{l}({c})" for l, c in top)
                lines.append(f"- **{label}**: {top_str}\n")

        lines.append("\n---\n\n")
        lines.append("## 逐镜头分析\n\n")

        for shot in shots:
            sn = shot.shot_number
            secs = f"{shot.start_time_sec:.1f}s - {shot.end_time_sec:.1f}s"
            lines.append(f"### 镜头 {sn} ({secs})\n\n")

            # Thumbnail
            kf_paths = json.loads(shot.keyframe_paths) if shot.keyframe_paths else []
            if kf_paths:
                lines.append(f"![镜头{sn}](/api/frames/{kf_paths[0]})\n\n")

            if shot.overall_notes:
                lines.append(f"{shot.overall_notes}\n\n")

            # Dimension details
            shot_dims = sorted(
                [d for d in dimensions if d.shot_id == shot.id],
                key=lambda d: all_dims.index(d.dimension_name) if d.dimension_name in all_dims else 99
            )
            if shot_dims:
                lines.append("| 维度 | 评分 | 类型 | 分析 |\n")
                lines.append("|------|------|------|------|\n")
                for d in shot_dims:
                    dim_label_map = {
                        "shot_scale": "景别", "composition": "构图", "camera_angle": "角度",
                        "lens": "焦段", "lighting_type": "灯光类型", "lighting_condition": "灯光条件",
                        "camera_movement": "运镜", "color": "色彩"
                    }
                    display = dim_label_map.get(d.dimension_name, d.dimension_name)
                    score = str(d.score) if d.score else "-"
                    label = d.label or "-"
                    notes = d.notes or "-"
                    lines.append(f"| {display} | {score} | {label} | {notes} |\n")
                lines.append("\n")

        report_md = "".join(lines)
        report_path = os.path.join(JOBS_DIR, job_id, "report.md")

        def _write():
            with open(report_path, "w") as f:
                f.write(report_md)

        await asyncio.to_thread(_write)

        return report_md
