import asyncio
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Optional


def run_cancellable(
    args: Sequence[str],
    *,
    timeout: float,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command while allowing a thread-safe cancellation callback to stop it."""
    if cancel_check and cancel_check():
        raise asyncio.CancelledError()

    process = subprocess.Popen(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    started = time.monotonic()

    while True:
        if cancel_check and cancel_check():
            _terminate_process(process)
            raise asyncio.CancelledError()

        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            _terminate_process(process)
            raise subprocess.TimeoutExpired(args, timeout)

        try:
            stdout, stderr = process.communicate(timeout=min(0.2, timeout - elapsed))
        except subprocess.TimeoutExpired:
            continue

        if cancel_check and cancel_check():
            raise asyncio.CancelledError()
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        process.communicate()
        return

    try:
        process.terminate()
    except OSError:
        pass

    try:
        process.wait(timeout=1.0)
        process.communicate()
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        process.kill()
    except OSError:
        pass
    process.wait()
    process.communicate()
