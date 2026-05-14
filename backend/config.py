import os
import shutil
import sqlite3
import time
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(primary_name: str, legacy_name: str, default: str = "") -> str:
    return os.getenv(primary_name) or os.getenv(legacy_name) or default


# Allow overriding data directory via env (for Electron persistent user data)
_DATA_ROOT = _env("FRAMEBENCH_DATA_DIR", "FILM_MASTER_DATA_DIR", BASE_DIR)
DATA_DIR = os.path.join(_DATA_ROOT, "data")
JOBS_DIR = os.path.join(DATA_DIR, "jobs")
DB_PATH = os.path.join(_DATA_ROOT, "film_master.db")
LOCAL_API_TOKEN = _env("FRAMEBENCH_LOCAL_TOKEN", "FILM_MASTER_LOCAL_TOKEN")
FFMPEG_BIN = _env("FRAMEBENCH_FFMPEG_BIN", "FILM_MASTER_FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = _env("FRAMEBENCH_FFPROBE_BIN", "FILM_MASTER_FFPROBE_BIN", "ffprobe")
MOONSHOT_API_KEY = os.getenv("MOONSHOT_API_KEY", "")
MOONSHOT_MODEL = "kimi-k2.6"
MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"
MAX_VIDEO_SIZE_MB = 2048
MAX_SHOTS = 300
BATCH_SIZE = 10  # Concurrent shots per batch (Moonshot Tier1: 50 concurrency)
JOB_DIR_PREFIX = os.path.join(JOBS_DIR, "")
SECRET_SETTING_KEYS = ("analysis_api_key", "storyboard_api_key", "moonshot_api_key")


def _sqlite_count(conn, table: str, where: str = "", params: tuple = ()) -> int:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not table_exists:
        return 0
    query = f"SELECT COUNT(*) FROM {table}"
    if where:
        query += f" WHERE {where}"
    return int(conn.execute(query, params).fetchone()[0])


def _db_stats(db_path: str) -> dict:
    if not os.path.exists(db_path):
        return {"exists": False, "jobs": 0, "shots": 0, "settings": 0, "secret_settings": 0}

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            secret_keys = ",".join(["?"] * len(SECRET_SETTING_KEYS))
            return {
                "exists": True,
                "jobs": _sqlite_count(conn, "jobs"),
                "shots": _sqlite_count(conn, "shots"),
                "settings": _sqlite_count(conn, "system_settings"),
                "secret_settings": _sqlite_count(
                    conn,
                    "system_settings",
                    f"key IN ({secret_keys}) AND value IS NOT NULL AND value != ''",
                    SECRET_SETTING_KEYS,
                ),
            }
        finally:
            conn.close()
    except sqlite3.Error:
        return {"exists": True, "jobs": 0, "shots": 0, "settings": 0, "secret_settings": 0}


def _legacy_data_roots() -> list[str]:
    app_support = os.path.expanduser("~/Library/Application Support")
    roots = [
        os.path.join(app_support, "Framebench"),
        os.path.join(app_support, "film-master"),
        os.path.join(app_support, "拉片工作台"),
        BASE_DIR,
        os.path.join(BASE_DIR, "data"),
    ]

    seen = set()
    unique_roots = []
    for root in roots:
        abs_root = os.path.abspath(root)
        if abs_root == os.path.abspath(_DATA_ROOT) or abs_root in seen:
            continue
        seen.add(abs_root)
        unique_roots.append(root)
    return unique_roots


def _db_candidates_for_root(root: str) -> list[str]:
    return [
        os.path.join(root, "film_master.db"),
        os.path.join(root, "data", "film_master.db"),
    ]


def _jobs_dir_for_db(db_path: str) -> str:
    db_dir = os.path.dirname(db_path)
    if os.path.basename(db_dir) == "data":
        return os.path.join(db_dir, "jobs")
    return os.path.join(db_dir, "data", "jobs")


def _best_legacy_db() -> tuple[Optional[str], dict]:
    best_path = None
    best_stats = {"exists": False, "jobs": 0, "shots": 0, "settings": 0, "secret_settings": 0}
    best_score = (-1, -1, -1, -1.0)

    for root in _legacy_data_roots():
        for candidate in _db_candidates_for_root(root):
            if os.path.abspath(candidate) == os.path.abspath(DB_PATH):
                continue
            stats = _db_stats(candidate)
            if not stats["exists"]:
                continue

            score = (
                stats["jobs"],
                stats["secret_settings"],
                stats["settings"],
                os.path.getmtime(candidate),
            )
            if score > best_score:
                best_path = candidate
                best_stats = stats
                best_score = score

    return best_path, best_stats


def _target_needs_legacy_data() -> bool:
    stats = _db_stats(DB_PATH)
    if not stats["exists"]:
        return True
    return stats["jobs"] == 0 and stats["secret_settings"] == 0


def _copy_legacy_data_if_needed():
    legacy_db, legacy_stats = _best_legacy_db()
    if not legacy_db or not (legacy_stats["jobs"] > 0 or legacy_stats["secret_settings"] > 0):
        return

    if _target_needs_legacy_data():
        if os.path.exists(DB_PATH):
            backup_path = f"{DB_PATH}.bak-{time.strftime('%Y%m%d%H%M%S')}"
            shutil.copy2(DB_PATH, backup_path)
        shutil.copy2(legacy_db, DB_PATH)

    legacy_jobs = _jobs_dir_for_db(legacy_db)
    if os.path.isdir(legacy_jobs) and not os.listdir(JOBS_DIR):
        shutil.copytree(legacy_jobs, JOBS_DIR, dirs_exist_ok=True)


def ensure_data_root():
    """Create the active data root and restore data from known legacy locations."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(JOBS_DIR, exist_ok=True)

    if os.path.abspath(_DATA_ROOT) == os.path.abspath(BASE_DIR):
        return

    _copy_legacy_data_if_needed()
