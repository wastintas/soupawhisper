"""Entry point for SoupaWhisper: python -m soupawhisper."""

import argparse
import os

# Force FFmpeg/libav error messages to ASCII-safe English.
# PyAV's Cython error handler crashes on non-ASCII (e.g. pt_BR locale).
os.environ["LC_MESSAGES"] = "C"
import shutil
import signal
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from soupawhisper import __version__
from soupawhisper import audio, clipboard, history, notify, transcribe
from soupawhisper.config import Config, ERROR_LOG_PATH


def _load_dotenv() -> None:
    """Load variables from .env file in the project directory if it exists."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            key, _, value = line.partition("=")
            if key and value:
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()
from soupawhisper.hotkeys import hotkey_display_name, resolve_hotkey, run_loop


class Dictation:
    """Orchestrates recording, transcription, clipboard, and tray updates."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.model = None
        self.model_name = config.model
        self.model_loaded = threading.Event()
        self.model_error: str | None = None
        self.running = threading.Event()
        self.running.set()
        self.tray = None
        self._record_process = None
        self._audio_path: Path | None = None
        self._record_start: float = 0.0
        self._can_paste = shutil.which("ydotool") is not None

    def start(self) -> None:
        """Start loading the model. Call after tray is set."""
        self._start_model_load(self.model_name)

    def _notify(self, title: str, message: str, icon: str = "dialog-information", timeout_ms: int = 2000) -> None:
        if self.config.notifications and not self.tray:
            notify.send(title, message, icon, timeout_ms)

    def _update_tray(self, state: str, model: str | None = None) -> None:
        if self.tray:
            self.tray.update(state, model=model)

    # -- Model loading --

    def _start_model_load(self, model_name: str) -> None:
        self.model_name = model_name
        self.model = None
        self.model_error = None
        self.model_loaded.clear()
        print(f"Loading model ({model_name})...")
        self._notify("Loading model...", model_name, "emblem-synchronizing", 5000)
        self._update_tray("loading", model=model_name)
        threading.Thread(target=self._load_model, daemon=True).start()

    def _load_model(self) -> None:
        try:
            self.model = transcribe.load_model(
                self.model_name, self.config.device, self.config.compute_type,
            )
            self.model_loaded.set()
            hotkey_name = hotkey_display_name(resolve_hotkey(self.config.hotkey))
            print(f"Model loaded: {self.model_name}. Ready!")
            print(f"Hold [{hotkey_name}] to record, release to transcribe.")
            print(f"Hold [{hotkey_name}] + 1/2/3/4 to switch model.")
            self._notify("Ready!", f"Model: {self.model_name}", "emblem-ok-symbolic", 3000)
            self._update_tray("ready", model=self.model_name)
        except Exception as exc:
            self.model_error = str(exc)
            self.model_loaded.set()
            print(f"Failed to load model: {exc}")
            if "cudnn" in str(exc).lower() or "cuda" in str(exc).lower():
                print("Hint: set device = cpu in config, or install cuDNN.")
            self._update_tray("error")

    def switch_model(self, model_name: str) -> None:
        """Switch to a different whisper model."""
        if model_name == self.model_name:
            self._notify("Model", f"Already using {model_name}", "dialog-information", 2000)
            return
        print(f"Switching to {model_name}...")
        self._start_model_load(model_name)

    # -- Recording --

    def _on_hotkey_press(self) -> None:
        if self._record_process or self.model_error:
            return

        self._audio_path = audio.create_temp_wav()
        self._record_process = audio.start_recording(self._audio_path)
        self._record_start = time.time()

        hotkey_name = hotkey_display_name(resolve_hotkey(self.config.hotkey))
        print("Recording...")
        self._notify("Recording...", f"Release {hotkey_name} when done", "audio-input-microphone", 30000)
        self._update_tray("recording")

    def _on_hotkey_release(self) -> None:
        if not self._record_process:
            return

        audio.stop_recording(self._record_process)
        self._record_process = None
        duration = time.time() - self._record_start

        print("Transcribing...")
        self._notify("Transcribing...", "Processing your speech", "emblem-synchronizing", 30000)
        self._update_tray("transcribing")

        threading.Thread(
            target=self._transcribe_and_paste,
            args=(self._audio_path, duration),
            daemon=True,
        ).start()

    def _on_model_switch(self, model_name: str) -> None:
        self._cancel_recording()
        self.switch_model(model_name)

    def _cancel_recording(self) -> None:
        if self._record_process:
            audio.stop_recording(self._record_process)
            self._record_process = None
        if self._audio_path and self._audio_path.exists():
            self._audio_path.unlink()
            self._audio_path = None
        print("Recording cancelled.")
        self._update_tray("ready")

    # -- Transcription --

    def _transcribe_and_paste(self, audio_path: Path, duration: float) -> None:
        try:
            self.model_loaded.wait()
            if self.model_error:
                self._notify("Error", "Model failed to load", "dialog-error", 3000)
                self._update_tray("error")
                return

            text = transcribe.transcribe(
                self.model, audio_path, self.config.language,
            )

            if not text:
                print("No speech detected")
                self._notify("No speech detected", "Try speaking louder", "dialog-warning", 2000)
                return

            clipboard.copy(text)

            if self.config.auto_paste and self._can_paste:
                clipboard.paste()

            history.save(text, self.model_name, duration)

            preview = text[:100] + "..." if len(text) > 100 else text
            print(f"Transcribed: {text}")
            self._notify("Copied!", preview, "emblem-ok-symbolic", 3000)

        except Exception as exc:
            tb = traceback.format_exc()
            print(f"Error: {exc}\n{tb}")
            ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(ERROR_LOG_PATH, "a") as f:
                f.write(f"--- {datetime.now().isoformat()} ---\n{tb}\n")
            self._notify("Error", str(exc)[:50], "dialog-error", 3000)
        finally:
            if audio_path.exists():
                audio_path.unlink()
            self._update_tray("ready")

    # -- Lifecycle --

    def stop(self) -> None:
        """Stop the dictation loop and exit."""
        print("\nExiting...")
        self.running.clear()
        from soupawhisper import tray as tray_module
        if self.tray:
            tray_module.quit_main_loop()

    def run(self) -> None:
        """Run the hotkey event loop (blocking)."""
        hotkey = resolve_hotkey(self.config.hotkey)
        run_loop(
            hotkey=hotkey,
            on_press=self._on_hotkey_press,
            on_release=self._on_hotkey_release,
            on_model_switch=self._on_model_switch,
            running=self.running,
        )


def transcribe_file(filepath: str, config: Config, model_override: str | None = None) -> str:
    """Transcribe an audio file and return the text."""
    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    model_name = model_override or config.model
    print(f"Loading model ({model_name})...")
    model = transcribe.load_model(model_name, config.device, config.compute_type)

    print(f"Transcribing: {filepath}")
    return transcribe.transcribe(model, Path(filepath), config.language)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="SoupaWhisper — Push-to-talk voice dictation")
    parser.add_argument("-v", "--version", action="version", version=f"SoupaWhisper {__version__}")
    parser.add_argument("-f", "--file", help="Transcribe an audio file")
    parser.add_argument("-m", "--model", help="Model override (base, small, medium, large-v3)")
    parser.add_argument("-o", "--output", help="Save transcription to a file instead of printing")
    parser.add_argument("--no-tray", action="store_true", help="Disable system tray icon")
    args = parser.parse_args()

    print(f"SoupaWhisper v{__version__}")

    config = Config.load()

    if args.file:
        text = transcribe_file(args.file, config, args.model)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"Saved to: {args.output}")
        else:
            print(f"\n{text}")
        return

    print(f"Config: {config}")
    clipboard.check_dependencies()

    dictation = Dictation(config)
    signal.signal(signal.SIGINT, lambda _sig, _frame: dictation.stop())

    from soupawhisper import tray as tray_module

    use_tray = not args.no_tray and tray_module.is_available()

    if use_tray:
        dictation.tray = tray_module.TrayIcon(dictation)
        dictation.start()
        threading.Thread(target=dictation.run, daemon=True).start()
        print("Tray icon active.")
        tray_module.run_main_loop()
        dictation.running.clear()
        os._exit(0)
    else:
        if not args.no_tray:
            print("Tray icon unavailable (install 'AppIndicator' GNOME extension)")
        dictation.start()
        dictation.run()


if __name__ == "__main__":
    main()
