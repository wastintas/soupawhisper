"""Keyboard hotkey capture via evdev."""

import select
from collections.abc import Callable
from typing import Protocol

import evdev
from evdev import ecodes

SKIP_DEVICE_NAMES = ("mouse", "touchpad", "trackpad", "trackpoint", "touchscreen")

MODEL_SLOTS: dict[int, str] = {
    ecodes.KEY_1: "base",
    ecodes.KEY_2: "small",
    ecodes.KEY_3: "medium",
    ecodes.KEY_4: "large-v3",
}

KEY_NAME_MAP: dict[str, int] = {
    "f1": ecodes.KEY_F1, "f2": ecodes.KEY_F2, "f3": ecodes.KEY_F3,
    "f4": ecodes.KEY_F4, "f5": ecodes.KEY_F5, "f6": ecodes.KEY_F6,
    "f7": ecodes.KEY_F7, "f8": ecodes.KEY_F8, "f9": ecodes.KEY_F9,
    "f10": ecodes.KEY_F10, "f11": ecodes.KEY_F11, "f12": ecodes.KEY_F12,
    "alt_r": ecodes.KEY_RIGHTALT, "alt_l": ecodes.KEY_LEFTALT,
    "ctrl_r": ecodes.KEY_RIGHTCTRL, "ctrl_l": ecodes.KEY_LEFTCTRL,
    "shift_r": ecodes.KEY_RIGHTSHIFT, "shift_l": ecodes.KEY_LEFTSHIFT,
    "super_r": ecodes.KEY_RIGHTMETA, "super_l": ecodes.KEY_LEFTMETA,
    "scroll_lock": ecodes.KEY_SCROLLLOCK, "pause": ecodes.KEY_PAUSE,
    "insert": ecodes.KEY_INSERT, "caps_lock": ecodes.KEY_CAPSLOCK,
}

SELECT_TIMEOUT_SECONDS = 1.0


class RunningFlag(Protocol):
    """Protocol for a flag that signals the loop should keep running."""
    def is_set(self) -> bool: ...


def resolve_hotkey(key_name: str) -> int:
    """Convert a key name string to an evdev keycode."""
    name = key_name.lower()
    if name in KEY_NAME_MAP:
        return KEY_NAME_MAP[name]

    attr = f"KEY_{name.upper()}"
    if hasattr(ecodes, attr):
        return getattr(ecodes, attr)

    print(f"Unknown key: {key_name}, defaulting to F12")
    return ecodes.KEY_F12


def hotkey_display_name(keycode: int) -> str:
    """Get the human-readable name for a keycode."""
    return ecodes.KEY.get(keycode, str(keycode))


def find_keyboards() -> list[evdev.InputDevice]:
    """Find keyboard input devices, excluding mice and touchpads."""
    devices: list[evdev.InputDevice] = []
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        name_lower = dev.name.lower()

        if _should_skip_device(dev, name_lower):
            dev.close()
            continue

        devices.append(dev)

    return devices


def _should_skip_device(dev: evdev.InputDevice, name_lower: str) -> bool:
    """Check if a device should be excluded from keyboard detection."""
    if any(skip in name_lower for skip in SKIP_DEVICE_NAMES):
        return True

    caps = dev.capabilities()
    if ecodes.EV_REL in caps:
        return True

    if ecodes.EV_KEY not in caps:
        return True

    key_codes = caps[ecodes.EV_KEY]
    return ecodes.KEY_A not in key_codes or ecodes.KEY_Z not in key_codes


def run_loop(
    hotkey: int,
    on_press: Callable[[], None],
    on_release: Callable[[], None],
    on_model_switch: Callable[[str], None],
    running: RunningFlag,
) -> None:
    """Main event loop: listen for hotkey presses on all keyboards."""
    keyboards = find_keyboards()
    if not keyboards:
        print("Error: No keyboard devices found.")
        print("Make sure your user is in the 'input' group:")
        print("  sudo usermod -aG input $USER")
        print("Then log out and back in.")
        return

    print(f"Monitoring {len(keyboards)} keyboard(s): {', '.join(d.name for d in keyboards)}")

    hotkey_held = False
    is_recording = False

    while running.is_set():
        readable, _, _ = select.select(keyboards, [], [], SELECT_TIMEOUT_SECONDS)
        for dev in readable:
            try:
                for event in dev.read():
                    if event.type != ecodes.EV_KEY:
                        continue

                    if event.code == hotkey:
                        if event.value == 1:
                            hotkey_held = True
                            is_recording = True
                            on_press()
                        elif event.value == 0:
                            hotkey_held = False
                            if is_recording:
                                is_recording = False
                                on_release()

                    elif hotkey_held and event.value == 1 and event.code in MODEL_SLOTS:
                        is_recording = False
                        on_model_switch(MODEL_SLOTS[event.code])

            except OSError:
                keyboards.remove(dev)
