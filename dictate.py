#!/usr/bin/env python3
"""
SoupaWhisper - Voice dictation tool using faster-whisper.
Hold the hotkey to record, release to transcribe and copy to clipboard.
"""

import argparse
import configparser
import subprocess
import tempfile
import threading
import signal
import sys
import os
import select
from pathlib import Path

import evdev
from evdev import ecodes
from faster_whisper import WhisperModel

__version__ = "0.1.0"

# Load configuration
CONFIG_PATH = Path.home() / ".config" / "soupawhisper" / "config.ini"


def load_config():
    config = configparser.ConfigParser()

    # Defaults
    defaults = {
        "model": "base.en",
        "device": "cpu",
        "compute_type": "int8",
        "key": "f12",
        "auto_type": "true",
        "notifications": "true",
    }

    if CONFIG_PATH.exists():
        config.read(CONFIG_PATH)

    return {
        "model": config.get("whisper", "model", fallback=defaults["model"]),
        "device": config.get("whisper", "device", fallback=defaults["device"]),
        "compute_type": config.get("whisper", "compute_type", fallback=defaults["compute_type"]),
        "language": config.get("whisper", "language", fallback="auto"),
        "key": config.get("hotkey", "key", fallback=defaults["key"]),
        "auto_type": config.getboolean("behavior", "auto_type", fallback=True),
        "notifications": config.getboolean("behavior", "notifications", fallback=True),
    }


CONFIG = load_config()


def get_hotkey_code(key_name):
    """Map key name to evdev key code."""
    key_name = key_name.lower()
    # Map common names to evdev key codes
    key_map = {
        "f1": ecodes.KEY_F1, "f2": ecodes.KEY_F2, "f3": ecodes.KEY_F3,
        "f4": ecodes.KEY_F4, "f5": ecodes.KEY_F5, "f6": ecodes.KEY_F6,
        "f7": ecodes.KEY_F7, "f8": ecodes.KEY_F8, "f9": ecodes.KEY_F9,
        "f10": ecodes.KEY_F10, "f11": ecodes.KEY_F11, "f12": ecodes.KEY_F12,
        "alt_r": ecodes.KEY_RIGHTALT, "alt_l": ecodes.KEY_LEFTALT,
        "ctrl_r": ecodes.KEY_RIGHTCTRL, "ctrl_l": ecodes.KEY_LEFTCTRL,
        "shift_r": ecodes.KEY_RIGHTSHIFT, "shift_l": ecodes.KEY_LEFTSHIFT,
        "super_r": ecodes.KEY_RIGHTMETA, "super_l": ecodes.KEY_LEFTMETA,
        "scroll_lock": ecodes.KEY_SCROLLLOCK, "pause": ecodes.KEY_PAUSE,
        "insert": ecodes.KEY_INSERT, "home": ecodes.KEY_HOME,
        "end": ecodes.KEY_END, "page_up": ecodes.KEY_PAGEUP,
        "page_down": ecodes.KEY_PAGEDOWN,
        "caps_lock": ecodes.KEY_CAPSLOCK,
    }
    if key_name in key_map:
        return key_map[key_name]
    # Try evdev KEY_ constant directly
    attr = f"KEY_{key_name.upper()}"
    if hasattr(ecodes, attr):
        return getattr(ecodes, attr)
    print(f"Unknown key: {key_name}, defaulting to F12")
    return ecodes.KEY_F12


HOTKEY = get_hotkey_code(CONFIG["key"])
MODEL_SIZE = CONFIG["model"]
DEVICE = CONFIG["device"]
COMPUTE_TYPE = CONFIG["compute_type"]
AUTO_TYPE = CONFIG["auto_type"]
NOTIFICATIONS = CONFIG["notifications"]


MODEL_SLOTS = {
    ecodes.KEY_1: "base",
    ecodes.KEY_2: "small",
    ecodes.KEY_3: "medium",
    ecodes.KEY_4: "large-v3",
}


class Dictation:
    def __init__(self):
        self.recording = False
        self.record_process = None
        self.temp_file = None
        self.model = None
        self.model_name = MODEL_SIZE
        self.model_loaded = threading.Event()
        self.model_error = None
        self.running = True
        self.hotkey_held = False

        # Load model in background
        self._start_model_load(self.model_name)

    def _start_model_load(self, model_name):
        self.model_name = model_name
        self.model = None
        self.model_error = None
        self.model_loaded.clear()
        print(f"Loading Whisper model ({model_name})...")
        self.notify("Loading model...", f"{model_name}", "emblem-synchronizing", 5000)
        threading.Thread(target=self._load_model, daemon=True).start()

    def _load_model(self):
        try:
            self.model = WhisperModel(self.model_name, device=DEVICE, compute_type=COMPUTE_TYPE)
            self.model_loaded.set()
            hotkey_name = ecodes.KEY[HOTKEY] if HOTKEY in ecodes.KEY else str(HOTKEY)
            print(f"Model loaded: {self.model_name}. Ready!")
            print(f"Hold [{hotkey_name}] to record, release to transcribe.")
            print(f"Hold [{hotkey_name}] + 1=base, 2=small, 3=medium, 4=large-v3")
            print("Press Ctrl+C to quit.")
            self.notify("Ready!", f"Model: {self.model_name}", "emblem-ok-symbolic", 3000)
        except Exception as e:
            self.model_error = str(e)
            self.model_loaded.set()
            print(f"Failed to load model: {e}")
            if "cudnn" in str(e).lower() or "cuda" in str(e).lower():
                print("Hint: Try setting device = cpu in your config, or install cuDNN.")

    def switch_model(self, model_name):
        if model_name == self.model_name:
            print(f"Already using {model_name}")
            self.notify("Model", f"Already using {model_name}", "dialog-information", 2000)
            return
        print(f"Switching to {model_name}...")
        self._start_model_load(model_name)

    def notify(self, title, message, icon="dialog-information", timeout=2000):
        """Send a desktop notification."""
        if not NOTIFICATIONS:
            return
        subprocess.run(
            [
                "notify-send",
                "-a", "SoupaWhisper",
                "-i", icon,
                "-t", str(timeout),
                "-h", "string:x-canonical-private-synchronous:soupawhisper",
                title,
                message
            ],
            capture_output=True
        )

    def start_recording(self):
        if self.recording or self.model_error:
            return

        self.recording = True
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        self.temp_file.close()

        # Record using arecord (ALSA) - works on most Linux systems
        self.record_process = subprocess.Popen(
            [
                "arecord",
                "-f", "S16_LE",  # Format: 16-bit little-endian
                "-r", "16000",   # Sample rate: 16kHz (what Whisper expects)
                "-c", "1",       # Mono
                "-t", "wav",
                self.temp_file.name
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print("Recording...")
        hotkey_name = ecodes.KEY.get(HOTKEY, str(HOTKEY))
        self.notify("Recording...", f"Release {hotkey_name} when done", "audio-input-microphone", 30000)

    def stop_recording(self):
        if not self.recording:
            return

        self.recording = False

        if self.record_process:
            self.record_process.terminate()
            self.record_process.wait()
            self.record_process = None

        print("Transcribing...")
        self.notify("Transcribing...", "Processing your speech", "emblem-synchronizing", 30000)

        # Wait for model if not loaded yet
        self.model_loaded.wait()

        if self.model_error:
            print(f"Cannot transcribe: model failed to load")
            self.notify("Error", "Model failed to load", "dialog-error", 3000)
            return

        # Transcribe
        try:
            lang = CONFIG["language"] if CONFIG["language"] != "auto" else None
            segments, info = self.model.transcribe(
                self.temp_file.name,
                beam_size=5,
                vad_filter=True,
                language=lang,
            )

            text = " ".join(segment.text.strip() for segment in segments)

            if text:
                # Detect Wayland vs X11
                is_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland"

                if is_wayland:
                    # Copy to clipboard using wl-copy
                    process = subprocess.Popen(
                        ["wl-copy"],
                        stdin=subprocess.PIPE
                    )
                    process.communicate(input=text.encode())
                else:
                    # Copy to clipboard using xclip
                    process = subprocess.Popen(
                        ["xclip", "-selection", "clipboard"],
                        stdin=subprocess.PIPE
                    )
                    process.communicate(input=text.encode())

                # Auto-paste using dotool (works on Wayland/X11)
                if AUTO_TYPE:
                    import time
                    time.sleep(0.3)
                    subprocess.run(
                        ["dotool"],
                        input=b"key ctrl+v",
                        capture_output=True
                    )

                print(f"Transcribed: {text}")
                self.notify("Copied!", text[:100] + ("..." if len(text) > 100 else ""), "emblem-ok-symbolic", 3000)
            else:
                print("No speech detected")
                self.notify("No speech detected", "Try speaking louder", "dialog-warning", 2000)

        except Exception as e:
            print(f"Error: {e}")
            self.notify("Error", str(e)[:50], "dialog-error", 3000)
        finally:
            # Cleanup temp file
            if self.temp_file and os.path.exists(self.temp_file.name):
                os.unlink(self.temp_file.name)

    def _find_keyboards(self):
        """Find keyboard-only input devices, excluding mice/touchpads."""
        devices = []
        skip_names = ("mouse", "touchpad", "trackpad", "trackpoint", "touchscreen")
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            name_lower = dev.name.lower()
            # Skip anything that looks like a mouse/touchpad
            if any(s in name_lower for s in skip_names):
                dev.close()
                continue
            caps = dev.capabilities()
            # Skip devices with relative axes (mice)
            if ecodes.EV_REL in caps:
                dev.close()
                continue
            # Must have keyboard keys
            if ecodes.EV_KEY in caps:
                key_codes = caps[ecodes.EV_KEY]
                # Must have letter keys or F-keys (real keyboard)
                if ecodes.KEY_A in key_codes and ecodes.KEY_Z in key_codes:
                    devices.append(dev)
                else:
                    dev.close()
            else:
                dev.close()
        return devices

    def stop(self):
        print("\nExiting...")
        self.running = False
        os._exit(0)

    def run(self):
        keyboards = self._find_keyboards()
        if not keyboards:
            print("Error: No keyboard devices found.")
            print("Make sure your user is in the 'input' group:")
            print("  sudo usermod -aG input $USER")
            print("Then log out and back in.")
            sys.exit(1)

        print(f"Monitoring {len(keyboards)} keyboard device(s): {', '.join(d.name for d in keyboards)}")

        while self.running:
            r, _, _ = select.select(keyboards, [], [], 1.0)
            for dev in r:
                try:
                    for event in dev.read():
                        if event.type != ecodes.EV_KEY:
                            continue
                        if event.code == HOTKEY:
                            if event.value == 1:  # key down
                                self.hotkey_held = True
                                self.start_recording()
                            elif event.value == 0:  # key up
                                self.hotkey_held = False
                                if self.recording:
                                    self.stop_recording()
                        elif self.hotkey_held and event.value == 1 and event.code in MODEL_SLOTS:
                            # Hotkey + number = switch model, cancel recording
                            if self.recording:
                                self.recording = False
                                if self.record_process:
                                    self.record_process.terminate()
                                    self.record_process.wait()
                                    self.record_process = None
                                if self.temp_file and os.path.exists(self.temp_file.name):
                                    os.unlink(self.temp_file.name)
                                print("Recording cancelled.")
                            self.switch_model(MODEL_SLOTS[event.code])
                except OSError:
                    # Device disconnected
                    keyboards.remove(dev)


def check_dependencies():
    """Check that required system commands are available."""
    missing = []
    is_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland"

    # Audio recording
    if subprocess.run(["which", "arecord"], capture_output=True).returncode != 0:
        missing.append(("arecord", "alsa-utils"))

    # Clipboard tool
    clip_cmd = "wl-copy" if is_wayland else "xclip"
    if subprocess.run(["which", clip_cmd], capture_output=True).returncode != 0:
        missing.append((clip_cmd, clip_cmd))

    if missing:
        print("Missing dependencies:")
        for cmd, pkg in missing:
            print(f"  {cmd} - install with: sudo apt install {pkg}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="SoupaWhisper - Push-to-talk voice dictation"
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"SoupaWhisper {__version__}"
    )
    parser.parse_args()

    print(f"SoupaWhisper v{__version__}")
    print(f"Config: {CONFIG_PATH}")

    check_dependencies()

    dictation = Dictation()

    # Handle Ctrl+C gracefully
    def handle_sigint(sig, frame):
        dictation.stop()

    signal.signal(signal.SIGINT, handle_sigint)

    dictation.run()


if __name__ == "__main__":
    main()
