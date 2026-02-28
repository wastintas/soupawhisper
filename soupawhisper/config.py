"""Configuration loading and defaults."""

import configparser
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "soupawhisper"
CONFIG_PATH = CONFIG_DIR / "config.ini"
DATA_DIR = Path.home() / ".local" / "share" / "soupawhisper"
HISTORY_PATH = DATA_DIR / "history.jsonl"
ERROR_LOG_PATH = DATA_DIR / "error.log"


@dataclass
class Config:
    """Application configuration with sensible defaults."""

    model: str = "base"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "auto"
    hotkey: str = "ctrl_r"
    auto_paste: bool = True
    notifications: bool = True

    @classmethod
    def load(cls) -> "Config":
        """Load config from ~/.config/soupawhisper/config.ini, falling back to defaults."""
        parser = configparser.ConfigParser()
        if CONFIG_PATH.exists():
            parser.read(CONFIG_PATH)

        return cls(
            model=parser.get("whisper", "model", fallback=cls.model),
            device=parser.get("whisper", "device", fallback=cls.device),
            compute_type=parser.get("whisper", "compute_type", fallback=cls.compute_type),
            language=parser.get("whisper", "language", fallback=cls.language),
            hotkey=parser.get("hotkey", "key", fallback=cls.hotkey),
            auto_paste=parser.getboolean("behavior", "auto_type", fallback=cls.auto_paste),
            notifications=parser.getboolean("behavior", "notifications", fallback=cls.notifications),
        )
