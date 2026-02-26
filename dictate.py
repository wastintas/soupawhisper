#!/usr/bin/env python3
"""
SoupaWhisper - Voice dictation tool using faster-whisper.
Hold the hotkey to record, release to transcribe and copy to clipboard.
"""

import argparse
import configparser
import json
import subprocess
import tempfile
import threading
import traceback
import signal
import sys
import os
import select
import time
from datetime import datetime
from pathlib import Path

import evdev
from evdev import ecodes
from faster_whisper import WhisperModel

# Tray icon support is lazy-loaded to avoid GTK's setlocale() breaking encoding.
# Actual imports happen in _init_tray() only when the tray is used.
Gtk = GLib = AyatanaAppIndicator3 = None
TRAY_AVAILABLE = False


def _init_tray():
    """Lazy-load GTK/AppIndicator and fix locale after GTK init."""
    global Gtk, GLib, AyatanaAppIndicator3, TRAY_AVAILABLE
    import locale
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('AyatanaAppIndicator3', '0.1')
        from gi.repository import Gtk as _Gtk, GLib as _GLib
        from gi.repository import AyatanaAppIndicator3 as _AI3
        Gtk, GLib, AyatanaAppIndicator3 = _Gtk, _GLib, _AI3
        # GTK calls setlocale(LC_ALL, "") which can reset to C/ASCII.
        # Force it back to UTF-8.
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
        TRAY_AVAILABLE = True
    except (ImportError, ValueError, locale.Error):
        TRAY_AVAILABLE = False
    return TRAY_AVAILABLE

__version__ = "0.1.0"

# Load configuration
CONFIG_PATH = Path.home() / ".config" / "soupawhisper" / "config.ini"
HISTORY_PATH = Path.home() / ".local" / "share" / "soupawhisper" / "history.jsonl"


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


class TrayIcon:
    """System tray icon using AyatanaAppIndicator3."""

    def __init__(self, dictation):
        self.dictation = dictation
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "soupawhisper",
            "audio-input-microphone-symbolic",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("SoupaWhisper")
        self._build_menu()

    def _build_menu(self):
        menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label="Loading...")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        self.model_item = Gtk.MenuItem(label=f"Model: {self.dictation.model_name}")
        self.model_item.set_sensitive(False)
        menu.append(self.model_item)

        menu.append(Gtk.SeparatorMenuItem())

        models_item = Gtk.MenuItem(label="Switch model")
        models_menu = Gtk.Menu()
        for name in ["base", "small", "medium", "large-v3"]:
            item = Gtk.MenuItem(label=name)
            item.connect("activate", self._on_model_select, name)
            models_menu.append(item)
        models_item.set_submenu(models_menu)
        menu.append(models_item)

        history_item = Gtk.MenuItem(label="History")
        history_item.connect("activate", self._on_history)
        menu.append(history_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit)
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)

    def update(self, state, model=None):
        """Thread-safe tray update — schedules UI changes on the GTK main loop."""
        def _do_update():
            if state == "recording":
                self.indicator.set_icon_full("media-record-symbolic", "Recording")
                self.status_item.set_label("Recording...")
            elif state == "transcribing":
                self.indicator.set_icon_full("emblem-synchronizing-symbolic", "Transcribing")
                self.status_item.set_label("Transcribing...")
            elif state == "ready":
                self.indicator.set_icon_full("audio-input-microphone-symbolic", "Ready")
                self.status_item.set_label("Ready")
            elif state == "loading":
                self.indicator.set_icon_full("emblem-synchronizing-symbolic", "Loading")
                self.status_item.set_label("Loading model...")
            elif state == "error":
                self.indicator.set_icon_full("dialog-error-symbolic", "Error")
                self.status_item.set_label("Error")
            if model:
                self.model_item.set_label(f"Model: {model}")
            return False
        GLib.idle_add(_do_update)

    def _on_history(self, widget):
        HistoryWindow().win.show_all()

    def _on_model_select(self, widget, model_name):
        threading.Thread(
            target=self.dictation.switch_model, args=(model_name,), daemon=True
        ).start()

    def _on_quit(self, widget):
        self.dictation.running = False
        Gtk.main_quit()


class HistoryWindow:
    """Window showing transcription history with click-to-copy."""

    def __init__(self):
        self.win = Gtk.Window(title="SoupaWhisper - History")
        self.win.set_default_size(600, 500)
        self.win.set_position(Gtk.WindowPosition.CENTER)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.win.add(vbox)

        # Scrollable list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        vbox.pack_start(scrolled, True, True, 0)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self._on_row_activated)
        scrolled.add(self.listbox)

        # Bottom bar
        bottom = Gtk.Box(spacing=8)
        bottom.set_margin_start(8)
        bottom.set_margin_end(8)
        bottom.set_margin_top(4)
        bottom.set_margin_bottom(4)

        self.count_label = Gtk.Label()
        self.count_label.set_halign(Gtk.Align.START)
        bottom.pack_start(self.count_label, True, True, 0)

        self.copy_hint = Gtk.Label(label="Click a row to copy")
        self.copy_hint.set_halign(Gtk.Align.END)
        self.copy_hint.set_opacity(0.5)
        bottom.pack_end(self.copy_hint, False, False, 0)

        vbox.pack_end(bottom, False, False, 0)

        self._load_history()

    def _load_history(self):
        entries = []
        if HISTORY_PATH.exists():
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

        # Most recent first
        entries.reverse()

        for entry in entries:
            row = self._make_row(entry)
            self.listbox.add(row)

        self.count_label.set_text(f"{len(entries)} transcriptions")

    def _make_row(self, entry):
        row = Gtk.ListBoxRow()
        row._text = entry.get("text", "")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        # Header: timestamp | duration | model
        ts = entry.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            ts_display = dt.strftime("%d/%m/%Y %H:%M")
        except (ValueError, TypeError):
            ts_display = ts

        dur = entry.get("duration", 0)
        model = entry.get("model", "?")
        header = Gtk.Label(label=f"{ts_display}   {dur}s   {model}")
        header.set_halign(Gtk.Align.START)
        header.set_opacity(0.6)
        box.pack_start(header, False, False, 0)

        # Text (truncated for display)
        text = entry.get("text", "")
        display_text = text[:200] + "..." if len(text) > 200 else text
        label = Gtk.Label(label=display_text)
        label.set_halign(Gtk.Align.START)
        label.set_line_wrap(True)
        label.set_max_width_chars(70)
        label.set_selectable(False)
        box.pack_start(label, False, False, 0)

        row.add(box)
        return row

    def _on_row_activated(self, listbox, row):
        text = getattr(row, '_text', '')
        if not text:
            return
        is_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland"
        if is_wayland:
            proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
            proc.communicate(input=text.encode("utf-8"))
        else:
            proc = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
            proc.communicate(input=text.encode("utf-8"))
        self.copy_hint.set_text("Copied!")
        GLib.timeout_add(2000, lambda: self.copy_hint.set_text("Click a row to copy") or False)


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
        self.record_start_time = None
        self.tray = None

        # Load model in background
        self._start_model_load(self.model_name)

    def _update_tray(self, state, model=None):
        if self.tray:
            self.tray.update(state, model=model)

    def _start_model_load(self, model_name):
        self.model_name = model_name
        self.model = None
        self.model_error = None
        self.model_loaded.clear()
        print(f"Loading Whisper model ({model_name})...")
        self.notify("Loading model...", f"{model_name}", "emblem-synchronizing", 5000)
        self._update_tray("loading", model=model_name)
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
            self._update_tray("ready", model=self.model_name)
        except Exception as e:
            self.model_error = str(e)
            self.model_loaded.set()
            print(f"Failed to load model: {e}")
            if "cudnn" in str(e).lower() or "cuda" in str(e).lower():
                print("Hint: Try setting device = cpu in your config, or install cuDNN.")
            self._update_tray("error")

    def switch_model(self, model_name):
        if model_name == self.model_name:
            print(f"Already using {model_name}")
            self.notify("Model", f"Already using {model_name}", "dialog-information", 2000)
            return
        print(f"Switching to {model_name}...")
        self._start_model_load(model_name)

    @staticmethod
    def apply_voice_commands(text):
        """Replace voice commands with their corresponding characters."""
        import re
        replacements = [
            (r'\bpróximo item\b', '\n'),
            (r'\bnova linha\b', '\n'),
            (r'\bpula linha\b', '\n'),
            (r'\bparágrafo\b', '\n\n'),
            (r'\bponto final\b', '.'),
            (r'\bvírgula\b', ','),
            (r'\bponto de interrogação\b', '?'),
            (r'\bponto de exclamação\b', '!'),
            (r'\bdois pontos\b', ':'),
            (r'\bponto e vírgula\b', ';'),
            (r'\babre parênteses\b', '('),
            (r'\bfecha parênteses\b', ')'),
        ]
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        # Clean up extra spaces around punctuation
        text = re.sub(r'\s+([.,;:!?)\]])', r'\1', text)
        text = re.sub(r'([\[(])\s+', r'\1', text)
        return text.strip()

    def save_to_history(self, text):
        """Save transcription to history file."""
        duration = round(time.time() - self.record_start_time, 1) if self.record_start_time else 0
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "text": text,
            "model": self.model_name,
            "duration": duration,
        }
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

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
        self.record_start_time = time.time()
        print("Recording...")
        hotkey_name = ecodes.KEY.get(HOTKEY, str(HOTKEY))
        self.notify("Recording...", f"Release {hotkey_name} when done", "audio-input-microphone", 30000)
        self._update_tray("recording")

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
        self._update_tray("transcribing")

        # Wait for model if not loaded yet
        self.model_loaded.wait()

        if self.model_error:
            print(f"Cannot transcribe: model failed to load")
            self.notify("Error", "Model failed to load", "dialog-error", 3000)
            self._update_tray("error")
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
            text = self.apply_voice_commands(text)

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

                self.save_to_history(text)
                print(f"Transcribed: {text}")
                self.notify("Copied!", text[:100] + ("..." if len(text) > 100 else ""), "emblem-ok-symbolic", 3000)
            else:
                print("No speech detected")
                self.notify("No speech detected", "Try speaking louder", "dialog-warning", 2000)

        except Exception as e:
            tb = traceback.format_exc()
            print(f"Error: {e}\n{tb}")
            # Write traceback to file for debugging
            log_path = Path.home() / ".local" / "share" / "soupawhisper" / "error.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                f.write(f"--- {datetime.now().isoformat()} ---\n{tb}\n")
            self.notify("Error", str(e)[:50], "dialog-error", 3000)
        finally:
            # Cleanup temp file
            if self.temp_file and os.path.exists(self.temp_file.name):
                os.unlink(self.temp_file.name)
            self._update_tray("ready")

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
        if self.tray:
            GLib.idle_add(Gtk.main_quit)
        else:
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
                                self._update_tray("ready")
                            self.switch_model(MODEL_SLOTS[event.code])
                except OSError:
                    # Device disconnected
                    keyboards.remove(dev)


def transcribe_file(filepath, output=None, model_override=None):
    """Transcribe an audio file and print or save the result."""
    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    model_name = model_override or MODEL_SIZE
    print(f"Loading model ({model_name})...")
    model = WhisperModel(model_name, device=DEVICE, compute_type=COMPUTE_TYPE)

    print(f"Transcribing: {filepath}")
    lang = CONFIG["language"] if CONFIG["language"] != "auto" else None
    segments, info = model.transcribe(
        filepath,
        beam_size=5,
        vad_filter=True,
        language=lang,
    )

    text = " ".join(segment.text.strip() for segment in segments)
    text = Dictation.apply_voice_commands(text)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Saved to: {output}")
    else:
        print(f"\n{text}")


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
    parser.add_argument(
        "-f", "--file",
        help="Transcribe an audio file (mp3, wav, m4a, ogg, flac, mp4, etc.)"
    )
    parser.add_argument(
        "-m", "--model",
        help="Model to use for file transcription (base, small, medium, large-v3)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Save transcription to a text file instead of printing"
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Disable system tray icon"
    )
    args = parser.parse_args()

    print(f"SoupaWhisper v{__version__}")

    # File transcription mode
    if args.file:
        transcribe_file(args.file, args.output, args.model)
        return

    print(f"Config: {CONFIG_PATH}")

    check_dependencies()

    dictation = Dictation()

    # Handle Ctrl+C gracefully
    def handle_sigint(sig, frame):
        dictation.stop()

    signal.signal(signal.SIGINT, handle_sigint)

    use_tray = not args.no_tray and _init_tray()

    if use_tray:
        dictation.tray = TrayIcon(dictation)
        # Run evdev loop in a background thread; GTK main loop takes the main thread
        evdev_thread = threading.Thread(target=dictation.run, daemon=True)
        evdev_thread.start()
        print("Tray icon active.")
        Gtk.main()
        # GTK main loop exited — clean up
        dictation.running = False
        os._exit(0)
    else:
        if not TRAY_AVAILABLE and not args.no_tray:
            print("Tray icon unavailable (install python3-gi + gir1.2-ayatanaappindicator3-0.1)")
        dictation.run()


if __name__ == "__main__":
    main()
