# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SoupaWhisper is a push-to-talk voice dictation tool for Linux. Hold a hotkey to record, release to transcribe using faster-whisper, then auto-paste into the active window. Single-file Python app (`dictate.py`).

## Commands

```bash
poetry install                              # Install dependencies
poetry run python dictate.py                # Run manually
systemctl --user start soupawhisper         # Start service
systemctl --user stop soupawhisper          # Stop service
journalctl --user -u soupawhisper -f        # View logs
```

## Architecture

Everything lives in `dictate.py`. Key flow:

1. **Input capture** (`evdev`): reads `/dev/input/` devices directly — works on both X11 and Wayland/COSMIC
2. **Audio recording** (`arecord`): captures mic at 16kHz mono WAV
3. **Transcription** (`faster-whisper`): runs Whisper model locally on CPU
4. **Clipboard** (`wl-copy` on Wayland, `xclip` on X11)
5. **Auto-paste** (`dotool`): sends `key ctrl+v` via uinput — the only tool that reliably works on COSMIC/Wayland

### Hotkey handling

- `_find_keyboards()` filters evdev devices: excludes mice/touchpads by checking for `EV_REL` and skip-names. **Never open mouse devices** — can freeze the pointer.
- Main loop uses `select()` on keyboard file descriptors
- Hotkey alone = record/transcribe. Hotkey + 1/2/3/4 = switch model (base/small/medium/large-v3) live

### Wayland/COSMIC constraints

These tools do NOT work on COSMIC desktop:
- `pynput` (X11 only for global key capture)
- `wtype` (COSMIC doesn't support `zwp_virtual_keyboard_v1`)
- `xdotool` (X11 only)
- `ydotool key` (sends wrong keycodes on COSMIC)
- `evdev UInput` for simulating input (can freeze mouse/keyboard — dangerous)

What works: `evdev` for reading, `dotool` for writing, `wl-copy` for clipboard.

## Config

`~/.config/soupawhisper/config.ini` — hotkey, model, language, device, auto_type, notifications.

## Dependencies

- **Python**: `faster-whisper`, `evdev` (in pyproject.toml via Poetry)
- **System**: `arecord` (alsa-utils), `wl-copy`/`xclip`, `dotool`, `notify-send`
- **Permissions**: user must be in `input` group; `/dev/uinput` needs group read/write for dotool
