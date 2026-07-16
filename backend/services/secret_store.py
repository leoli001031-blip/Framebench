import os
import platform
import shutil
import subprocess

KEYCHAIN_SERVICE = "Framebench"
KEYCHAIN_MARKER = "__FRAMEBENCH_KEYCHAIN__"


def is_keychain_marker(value: str | None) -> bool:
    return value == KEYCHAIN_MARKER


def can_use_keychain() -> bool:
    return platform.system() == "Darwin" and shutil.which("security") is not None


def _run_security(args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return subprocess.run(
        ["security", *args],
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )


def get_keychain_secret(key: str) -> str | None:
    if not can_use_keychain():
        return None
    try:
        result = _run_security(["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key, "-w"])
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def set_keychain_secret(key: str, value: str) -> bool:
    if not can_use_keychain():
        return False
    try:
        if value:
            result = _run_security([
                "add-generic-password",
                "-U",
                "-s", KEYCHAIN_SERVICE,
                "-a", key,
                "-w", value,
            ])
            return result.returncode == 0
        delete_keychain_secret(key)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def delete_keychain_secret(key: str) -> bool:
    if not can_use_keychain():
        return False
    try:
        result = _run_security(["delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key])
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode in {0, 44}


def store_secret_value(key: str, value: str) -> str:
    if not value:
        delete_keychain_secret(key)
        return ""
    if set_keychain_secret(key, value):
        return KEYCHAIN_MARKER
    return value


def resolve_secret_value(key: str, stored_value: str | None, default: str = "") -> str:
    if is_keychain_marker(stored_value):
        return get_keychain_secret(key) or default
    return stored_value or default
