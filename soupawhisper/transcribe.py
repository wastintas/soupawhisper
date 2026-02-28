"""Speech-to-text transcription using faster-whisper."""

import os
import re
from pathlib import Path

from faster_whisper import WhisperModel

BEAM_SIZE = 1

VOICE_COMMANDS: list[tuple[str, str]] = [
    (r"\bpróximo item\b", "\n"),
    (r"\bnova linha\b", "\n"),
    (r"\bpula linha\b", "\n"),
    (r"\bparágrafo\b", "\n\n"),
    (r"\bponto final\b", "."),
    (r"\bvírgula\b", ","),
    (r"\bponto de interrogação\b", "?"),
    (r"\bponto de exclamação\b", "!"),
    (r"\bdois pontos\b", ":"),
    (r"\bponto e vírgula\b", ";"),
    (r"\babre parênteses\b", "("),
    (r"\bfecha parênteses\b", ")"),
]


def load_model(name: str, device: str, compute_type: str) -> WhisperModel:
    """Load a faster-whisper model."""
    return WhisperModel(
        name, device=device, compute_type=compute_type,
        cpu_threads=os.cpu_count() or 4,
    )


def transcribe(model: WhisperModel, audio_path: Path, language: str | None = None) -> str:
    """Transcribe an audio file and return the text."""
    resolved_language = language if language != "auto" else None
    segments, _info = model.transcribe(
        str(audio_path),
        beam_size=BEAM_SIZE,
        vad_filter=True,
        language=resolved_language,
    )
    raw_text = " ".join(segment.text.strip() for segment in segments)
    return apply_voice_commands(raw_text)


def apply_voice_commands(text: str) -> str:
    """Replace spoken punctuation commands with their characters."""
    for pattern, replacement in VOICE_COMMANDS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([.,;:!?)\]])", r"\1", text)
    text = re.sub(r"([\[(])\s+", r"\1", text)
    return text.strip()
