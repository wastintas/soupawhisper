# SoupaWhisper

Push-to-talk voice dictation for Linux (Wayland/GNOME) using [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

Hold a hotkey to record, release to transcribe, auto-paste into the active window.

## Requirements

- Linux with Wayland (GNOME, tested on Fedora 43)
- Python 3.12+
- System packages: `alsa-utils`, `wl-clipboard`, `ydotool`, `libnotify`
- User in `input` group

## Install

```bash
# System dependencies (Fedora)
sudo dnf install alsa-utils wl-clipboard ydotool libnotify

# Or use the install script
./install.sh

# Python dependencies
poetry install
```

## Usage

```bash
# Run directly
poetry run python -m soupawhisper

# Or via entry point
poetry run soupawhisper

# Transcribe a file
poetry run soupawhisper -f recording.wav

# Without tray icon
poetry run soupawhisper --no-tray

# As systemd service
systemctl --user start soupawhisper
```

## Hotkeys

| Key | Action |
|---|---|
| Hold F12 | Record |
| Release F12 | Transcribe + paste |
| F12 + 1 | Switch to base model |
| F12 + 2 | Switch to small model |
| F12 + 3 | Switch to medium model |
| F12 + 4 | Switch to large-v3 model |

## Config

`~/.config/soupawhisper/config.ini`:

```ini
[whisper]
model = base.en
device = cpu
compute_type = int8
language = auto

[hotkey]
key = f12

[behavior]
auto_type = true
notifications = true
```

## License

MIT
