import json
import logging
import os
from datetime import datetime, timezone
from typing import Literal

from backend.config import JOBS_DIR


logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 1000
ModelCallScope = Literal["job", "storyboard"]


def _model_calls_path(scope: ModelCallScope, scope_id: str, jobs_dir: str) -> str:
    if scope == "job":
        return os.path.join(jobs_dir, scope_id, "model_calls.jsonl")
    if scope == "storyboard":
        return os.path.join(jobs_dir, "storyboards", scope_id, "model_calls.jsonl")
    raise ValueError(f"Unsupported model call scope: {scope}")


def _usage_tokens(usage: dict | None) -> tuple[int, int, int]:
    usage = usage or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    return prompt_tokens, completion_tokens, total_tokens


def record_model_call(
    scope: ModelCallScope,
    scope_id: str,
    *,
    source: str,
    model: str,
    attempt: int,
    outcome: str,
    status_code: int | None,
    elapsed_ms: int | None,
    retryable: bool,
    usage: dict | None = None,
    error: str | None = None,
    shot_number: int | None = None,
    jobs_dir: str = JOBS_DIR,
) -> bool:
    """Append one model call without allowing accounting failures to escape."""
    try:
        path = _model_calls_path(scope, scope_id, jobs_dir)
        prompt_tokens, completion_tokens, total_tokens = _usage_tokens(usage)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "scope": scope,
            "scope_id": scope_id,
            "source": source,
            "model": model,
            "attempt": attempt,
            "outcome": outcome,
            "status_code": status_code,
            "elapsed_ms": elapsed_ms,
            "retryable": retryable,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "error": None if error is None else str(error)[:MAX_ERROR_LENGTH],
        }
        if shot_number is not None:
            record["shot_number"] = shot_number

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        logger.warning("Model call accounting failed: %s", exc)
        return False
