import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from backend.config import JOBS_DIR


logger = logging.getLogger(__name__)


def _usage_path(job_id: str, jobs_dir: str = JOBS_DIR) -> str:
    return os.path.join(jobs_dir, job_id, "token_usage.jsonl")


def append_token_usage(
    job_id: str,
    *,
    source: str,
    model: str,
    usage: dict,
    shot_number: Optional[int] = None,
    jobs_dir: str = JOBS_DIR,
):
    if not usage:
        return
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    if prompt_tokens <= 0 and completion_tokens <= 0 and total_tokens <= 0:
        return

    path = _usage_path(job_id, jobs_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "model": model,
        "shot_number": shot_number,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def try_append_token_usage(*args, **kwargs) -> bool:
    """Record optional accounting data without failing a successful model call."""
    try:
        append_token_usage(*args, **kwargs)
        return True
    except Exception as exc:
        logger.warning("Token usage accounting failed: %s", exc)
        return False


def summarize_token_usage(job_id: str, jobs_dir: str = JOBS_DIR) -> dict:
    path = _usage_path(job_id, jobs_dir)
    summary = {
        "path": path,
        "exists": os.path.exists(path),
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "by_source": {},
    }
    if not summary["exists"]:
        summary["by_source"] = []
        return summary

    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            source = record.get("source") or "unknown"
            prompt = int(record.get("prompt_tokens") or 0)
            completion = int(record.get("completion_tokens") or 0)
            total = int(record.get("total_tokens") or (prompt + completion))
            summary["calls"] += 1
            summary["prompt_tokens"] += prompt
            summary["completion_tokens"] += completion
            summary["total_tokens"] += total
            source_row = summary["by_source"].setdefault(source, {
                "source": source,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            })
            source_row["calls"] += 1
            source_row["prompt_tokens"] += prompt
            source_row["completion_tokens"] += completion
            source_row["total_tokens"] += total

    summary["by_source"] = list(summary["by_source"].values())
    return summary
