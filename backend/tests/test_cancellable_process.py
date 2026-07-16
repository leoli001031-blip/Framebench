import asyncio
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

from backend.services.cancellable_process import run_cancellable


class CancellableProcessTests(unittest.TestCase):
    def test_pre_cancelled_command_is_not_started(self):
        with patch("backend.services.cancellable_process.subprocess.Popen") as popen:
            with self.assertRaises(asyncio.CancelledError):
                run_cancellable(
                    [sys.executable, "-c", "print('unused')"],
                    timeout=5,
                    cancel_check=lambda: True,
                )

        popen.assert_not_called()

    def test_large_stderr_does_not_deadlock(self):
        result = run_cancellable(
            [sys.executable, "-c", "import sys; sys.stderr.write('x' * 200000)"],
            timeout=5,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stderr), 200000)

    @unittest.skipUnless(os.name == "posix", "SIGTERM escalation test requires POSIX")
    def test_running_command_is_killed_and_reaped(self):
        cancel_event = threading.Event()
        timer = threading.Timer(0.3, cancel_event.set)
        started = time.monotonic()
        timer.start()
        try:
            with self.assertRaises(asyncio.CancelledError):
                run_cancellable(
                    [
                        sys.executable,
                        "-c",
                        "import signal,time; signal.signal(signal.SIGTERM, lambda *_: None); time.sleep(30)",
                    ],
                    timeout=35,
                    cancel_check=cancel_event.is_set,
                )
        finally:
            timer.cancel()

        self.assertLess(time.monotonic() - started, 3.0)


if __name__ == "__main__":
    unittest.main()
