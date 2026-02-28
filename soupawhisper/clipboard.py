"""Clipboard and auto-paste for Wayland (wl-copy + ydotool)."""

import shutil
import subprocess
import sys
import time
from pathlib import Path

PASTE_DELAY_SECONDS = 0.3
YDOTOOLD_SOCKET = Path(f"/run/user/{__import__('os').getuid()}/.ydotool_socket")

REQUIRED_COMMANDS = {
    "arecord": "alsa-utils",
    "wl-copy": "wl-clipboard",
    "notify-send": "libnotify",
}

OPTIONAL_COMMANDS = {
    "ydotool": "ydotool",
}

_ydotoold_process: subprocess.Popen | None = None


def _ensure_ydotoold() -> bool:
    """Start ydotoold if not already running. Returns True if daemon is available."""
    global _ydotoold_process

    if YDOTOOLD_SOCKET.exists():
        return True

    if not shutil.which("ydotoold"):
        return False

    try:
        _ydotoold_process = subprocess.Popen(
            ["ydotoold"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait briefly for socket to appear
        for _ in range(10):
            time.sleep(0.1)
            if YDOTOOLD_SOCKET.exists():
                return True
        return False
    except OSError:
        return False


def copy(text: str) -> None:
    """Copy text to the Wayland clipboard using wl-copy."""
    process = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
    process.communicate(input=text.encode("utf-8"))


def paste() -> bool:
    """Simulate Ctrl+V using ydotool. Returns True on success."""
    if not _ensure_ydotoold():
        return False

    time.sleep(PASTE_DELAY_SECONDS)
    result = subprocess.run(
        ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
        capture_output=True,
    )
    return result.returncode == 0


def check_dependencies() -> None:
    """Verify required system commands are installed. Exits on failure."""
    missing: list[tuple[str, str]] = []
    for cmd, pkg in REQUIRED_COMMANDS.items():
        if not shutil.which(cmd):
            missing.append((cmd, pkg))

    if missing:
        print("Missing required dependencies:")
        for cmd, pkg in missing:
            print(f"  {cmd} — install: sudo dnf install {pkg}")
        sys.exit(1)

    for cmd, pkg in OPTIONAL_COMMANDS.items():
        if not shutil.which(cmd):
            print(f"Warning: {cmd} not found (sudo dnf install {pkg})")
            print("  Auto-paste will be disabled.")
