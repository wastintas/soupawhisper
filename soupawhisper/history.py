"""Transcription history stored as JSONL."""

import json
from datetime import datetime

from soupawhisper.config import HISTORY_PATH


def save(text: str, model: str, duration: float) -> None:
    """Append a transcription entry to the history file."""
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "text": text,
        "model": model,
        "duration": round(duration, 1),
    }
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load() -> list[dict]:
    """Load all history entries, most recent first."""
    if not HISTORY_PATH.exists():
        return []

    entries = []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    entries.reverse()
    return entries
