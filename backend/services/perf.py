from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any


def perf_now() -> float:
    return time.perf_counter()


def record_duration(job_dir: str, key: str, started_at: float, extra: dict[str, Any] | None = None):
    payload: dict[str, Any] = {key: round(time.perf_counter() - started_at, 3)}
    if extra:
        payload.update(extra)
    record_metrics(job_dir, payload)


def record_metrics(job_dir: str, metrics: dict[str, Any]):
    os.makedirs(job_dir, exist_ok=True)
    path = os.path.join(job_dir, "performance.json")
    data: dict[str, Any] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}

    data.update(metrics)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def directory_size_bytes(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total
