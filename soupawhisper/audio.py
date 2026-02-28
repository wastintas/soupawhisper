"""Audio recording via arecord (ALSA)."""

import subprocess
import tempfile
from pathlib import Path

SAMPLE_RATE = 16000
CHANNELS = 1
FORMAT = "S16_LE"


def create_temp_wav() -> Path:
    """Create a temporary WAV file path for recording."""
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    f.close()
    return Path(f.name)


def start_recording(output_path: Path) -> subprocess.Popen:
    """Start recording audio to a WAV file using arecord."""
    return subprocess.Popen(
        [
            "arecord",
            "-f", FORMAT,
            "-r", str(SAMPLE_RATE),
            "-c", str(CHANNELS),
            "-t", "wav",
            str(output_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_recording(process: subprocess.Popen) -> None:
    """Stop an active arecord process."""
    process.terminate()
    process.wait()
