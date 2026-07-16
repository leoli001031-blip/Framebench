#!/usr/bin/env python3
"""Measure packaged Framebench cold-start stages against an isolated DB copy."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import statistics
import subprocess
import tempfile
import time
from pathlib import Path


MILESTONE_PATTERNS = {
    "backend_log": "starting backend",
    "server_started": "Started server process",
    "startup_complete": "Application startup complete",
    "health": 'GET /api/health HTTP/1.1" 200',
    "first_screen": 'GET /api/jobs HTTP/1.1" 200',
}


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _app_is_running(app_path: Path) -> bool:
    executable = app_path / "Contents" / "MacOS" / "Framebench"
    result = subprocess.run(
        ["pgrep", "-f", str(executable)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _stop_app(process: subprocess.Popen, port: int) -> None:
    subprocess.run(
        ["osascript", "-e", 'tell application id "com.framebench.app" to quit'],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    listeners = subprocess.run(
        ["lsof", "-tiTCP:%d" % port, "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    for value in listeners.stdout.split():
        try:
            os.kill(int(value), signal.SIGTERM)
        except (OSError, ValueError):
            pass


def _duration_ms(start: float, end: float) -> float:
    return round((end - start) * 1000, 1)


def measure_once(app_path: Path, source_db: Path, timeout: float, run_number: int) -> dict:
    executable = app_path / "Contents" / "MacOS" / "Framebench"
    if not executable.is_file():
        raise FileNotFoundError(f"Framebench executable not found: {executable}")
    if not source_db.is_file():
        raise FileNotFoundError(f"Source DB not found: {source_db}")

    with tempfile.TemporaryDirectory(prefix=f"framebench-cold-start-{run_number}-") as tmp:
        root = Path(tmp)
        home = root / "home"
        data_root = root / "data-root"
        home.mkdir()
        data_root.mkdir()
        shutil.copy2(source_db, data_root / "film_master.db")

        port = _available_port()
        log_path = data_root / "logs" / "backend.log"
        env = os.environ.copy()
        env.update({
            "HOME": str(home),
            "FRAMEBENCH_DATA_DIR": str(data_root),
            "FRAMEBENCH_BACKEND_PORT": str(port),
            "FRAMEBENCH_LOCAL_TOKEN": "startup-smoke-token",
            "FRAMEBENCH_LOG_LEVEL": "info",
            "STEPFUN_API_KEY": "",
        })

        started = time.monotonic()
        process = subprocess.Popen(
            [str(executable)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        milestones: dict[str, float] = {}
        deadline = started + timeout

        try:
            while time.monotonic() < deadline:
                now = time.monotonic()
                content = _read_text(log_path)
                for name, pattern in MILESTONE_PATTERNS.items():
                    if name not in milestones and pattern in content:
                        milestones[name] = now
                if "first_screen" in milestones:
                    break
                if process.poll() is not None:
                    raise RuntimeError(f"Framebench exited before first screen: {process.returncode}")
                time.sleep(0.02)
            else:
                missing = [name for name in MILESTONE_PATTERNS if name not in milestones]
                raise TimeoutError(f"Cold start timed out; missing milestones: {', '.join(missing)}")
        finally:
            _stop_app(process, port)

        values = {
            "run": run_number,
            "electron_ms": _duration_ms(started, milestones["backend_log"]),
            "backend_unpack_import_ms": _duration_ms(
                milestones["backend_log"], milestones["server_started"]
            ),
            "database_startup_ms": _duration_ms(
                milestones["server_started"], milestones["startup_complete"]
            ),
            "health_check_ms": _duration_ms(
                milestones["startup_complete"], milestones["health"]
            ),
            "frontend_first_screen_ms": _duration_ms(
                milestones["health"], milestones["first_screen"]
            ),
            "total_ms": _duration_ms(started, milestones["first_screen"]),
        }
        time.sleep(0.5)
        return values


def _summary(results: list[dict]) -> dict:
    keys = [key for key in results[0] if key != "run"]
    return {
        key: {
            "median": round(statistics.median(row[key] for row in results), 1),
            "max": round(max(row[key] for row in results), 1),
        }
        for key in keys
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=Path("/Applications/Framebench.app"))
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home() / "Library" / "Application Support" / "film-master" / "film_master.db",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if _app_is_running(args.app):
        parser.error("Framebench is already running; quit it before measuring cold start")

    results = [
        measure_once(args.app, args.db, args.timeout, run_number)
        for run_number in range(1, args.runs + 1)
    ]
    payload = {"runs": results, "summary": _summary(results)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
