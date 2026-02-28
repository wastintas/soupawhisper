import evdev, select
from evdev import ecodes

skip_names = ("mouse", "touchpad", "trackpad", "touchscreen")
devs = []
for path in evdev.list_devices():
    dev = evdev.InputDevice(path)
    if any(s in dev.name.lower() for s in skip_names):
        dev.close()
        continue
    caps = dev.capabilities()
    if ecodes.EV_REL in caps:
        dev.close()
        continue
    if ecodes.EV_KEY in caps and ecodes.KEY_A in caps.get(ecodes.EV_KEY, []):
        devs.append(dev)
        print(f"  {dev.path}: {dev.name}")
    else:
        dev.close()

print("\nPressione qualquer tecla. Ctrl+C pra sair.\n")
while True:
    r, _, _ = select.select(devs, [], [])
    for d in r:
        for e in d.read():
            if e.type == ecodes.EV_KEY and e.value == 1:
                print(f"[{d.name}] code={e.code} name={ecodes.KEY.get(e.code, e.code)}")
