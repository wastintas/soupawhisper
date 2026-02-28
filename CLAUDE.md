# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SoupaWhisper is a push-to-talk voice dictation tool for Linux (Wayland/GNOME). Hold a hotkey to record, release to transcribe using faster-whisper, then auto-paste into the active window.

## Commands

```bash
poetry install                              # Install dependencies
poetry run python -m soupawhisper           # Run manually
poetry run soupawhisper                     # Run via script entry point
systemctl --user start soupawhisper         # Start service
systemctl --user stop soupawhisper          # Stop service
journalctl --user -u soupawhisper -f        # View logs
```

## Architecture

Multi-module package under `soupawhisper/`:

| Module | Responsibility |
|---|---|
| `config.py` | Load `~/.config/soupawhisper/config.ini`, dataclass with defaults |
| `audio.py` | Record via `arecord` (16kHz mono WAV) |
| `transcribe.py` | faster-whisper model loading, transcription, voice commands |
| `hotkeys.py` | evdev keyboard detection, hotkey event loop with `select()` |
| `clipboard.py` | `wl-copy` for clipboard, `ydotool` for Ctrl+V paste, dependency checks |
| `notify.py` | Desktop notifications via `notify-send` |
| `history.py` | Transcription history in `~/.local/share/soupawhisper/history.jsonl` |
| `tray.py` | GTK3/AyatanaAppIndicator3 tray icon (lazy-loaded) |
| `__main__.py` | CLI, `Dictation` orchestrator class, entry point |

### Key flow

1. **Input capture** (`evdev`): reads `/dev/input/` keyboard devices directly
2. **Audio recording** (`arecord`): captures mic at 16kHz mono WAV
3. **Transcription** (`faster-whisper`): runs Whisper model locally on CPU
4. **Clipboard** (`wl-copy`): copies text to Wayland clipboard
5. **Auto-paste** (`ydotool`): sends Ctrl+V via uinput

### Hotkey handling

- `find_keyboards()` in `hotkeys.py` filters evdev devices: excludes mice/touchpads by checking for `EV_REL` and skip-names. **Never open mouse devices** — can freeze the pointer.
- Main loop uses `select()` on keyboard file descriptors
- Hotkey alone = record/transcribe. Hotkey + 1/2/3/4 = switch model live

### Design principles

- **Wayland-only**: no X11 fallbacks, no `xclip`, no `xdotool`
- **Clean code**: single responsibility modules, type hints, dependency injection
- **No global state**: config passed as parameter via `Config` dataclass

## Config

`~/.config/soupawhisper/config.ini` — hotkey, model, language, device, auto_paste, notifications.

## Dependencies

- **Python ^3.12,<3.14**: `faster-whisper`, `evdev` (via Poetry)
- **System**: `arecord` (alsa-utils), `wl-copy` (wl-clipboard), `ydotool`, `notify-send` (libnotify)
- **Permissions**: user must be in `input` group; `/dev/uinput` needs `GROUP="input", MODE="0660"`

## Reference

The original single-file version is preserved in `.references_only/` for reference.
