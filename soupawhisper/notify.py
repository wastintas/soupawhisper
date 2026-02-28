"""Desktop notifications via notify-send."""

import subprocess

DEFAULT_TIMEOUT_MS = 2000


def send(
    title: str,
    message: str,
    icon: str = "dialog-information",
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> None:
    """Send a desktop notification using notify-send."""
    subprocess.run(
        [
            "notify-send",
            "-a", "SoupaWhisper",
            "-i", icon,
            "-t", str(timeout_ms),
            "-h", "string:x-canonical-private-synchronous:soupawhisper",
            title,
            message,
        ],
        capture_output=True,
    )
