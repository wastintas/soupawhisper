"""System tray via DBus StatusNotifierItem — no libappindicator needed.

Works on GNOME (with AppIndicator extension), KDE Plasma, and other
desktops supporting the StatusNotifierItem specification.
Only requires gi.repository.Gio and GLib (always available on GNOME).
"""

from __future__ import annotations

import os
import subprocess
import threading
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib

if TYPE_CHECKING:
    from soupawhisper.__main__ import Dictation

# -- Icons (themed, available on most desktops) --

ICON_READY = "audio-input-microphone-symbolic"
ICON_RECORDING = "media-record-symbolic"
ICON_WORKING = "emblem-synchronizing-symbolic"
ICON_ERROR = "dialog-error-symbolic"

AVAILABLE_MODELS = ["base", "small", "medium", "large-v3"]

# -- DBus constants --

SNI_INTERFACE = "org.kde.StatusNotifierItem"
DBUSMENU_INTERFACE = "com.canonical.dbusmenu"
WATCHER_BUS = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

# -- Menu item IDs --

_ID_STATUS = 1
_ID_MODEL = 2
_ID_SEP1 = 3
_ID_SWITCH = 4
_ID_MODEL_BASE = 5
_ID_MODEL_SMALL = 6
_ID_MODEL_MEDIUM = 7
_ID_MODEL_LARGE = 8
_ID_HISTORY = 9
_ID_SEP2 = 10
_ID_QUIT = 11

_MODEL_ACTIONS = {
    _ID_MODEL_BASE: "base",
    _ID_MODEL_SMALL: "small",
    _ID_MODEL_MEDIUM: "medium",
    _ID_MODEL_LARGE: "large-v3",
}

# -- DBus introspection XML --

SNI_XML = """<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <method name="Activate">
      <arg type="i" direction="in"/><arg type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" direction="in"/><arg type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" direction="in"/><arg type="s" direction="in"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>"""

MENU_XML = """<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/><arg type="s" direction="in"/>
      <arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/>
      <arg type="v" direction="in"/><arg type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" direction="in"/><arg type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in"/>
      <arg type="ai" direction="out"/><arg type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated">
      <arg type="u"/><arg type="i"/>
    </signal>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})"/><arg type="a(ias)"/>
    </signal>
  </interface>
</node>"""


# -- Helpers --

def _item(item_id: int, props: dict, children: list | None = None) -> GLib.Variant:
    """Build a DBusMenu item (ia{sv}av)."""
    sv = {k: _to_variant(v) for k, v in props.items()}
    return GLib.Variant("(ia{sv}av)", (item_id, sv, children or []))


def _to_variant(value) -> GLib.Variant:
    """Convert a Python value to the appropriate GLib.Variant."""
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, str):
        return GLib.Variant("s", value)
    if isinstance(value, int):
        return GLib.Variant("i", value)
    return value


# -- Main loop --

_main_loop: GLib.MainLoop | None = None


def is_available() -> bool:
    """Check if StatusNotifierWatcher is running on the session bus."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (WATCHER_BUS,)),
            GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, -1, None,
        )
        return result.unpack()[0]
    except Exception:
        return False


def run_main_loop() -> None:
    """Start the GLib main loop (blocking)."""
    global _main_loop
    _main_loop = GLib.MainLoop()
    _main_loop.run()


def quit_main_loop() -> None:
    """Quit the GLib main loop from any thread."""
    if _main_loop:
        _main_loop.quit()


class TrayIcon:
    """System tray icon via DBus StatusNotifierItem + DBusMenu."""

    def __init__(self, dictation: Dictation) -> None:
        self.dictation = dictation
        self._icon_name = ICON_READY
        self._status_label = "Loading..."
        self._model_name = dictation.model_name
        self._revision = 1
        self._connection: Gio.DBusConnection | None = None
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"

        self._sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self._menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]

        Gio.bus_own_name(
            Gio.BusType.SESSION, self._bus_name,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired, self._on_name_acquired, None,
        )

    def _on_bus_acquired(self, connection: Gio.DBusConnection, name: str) -> None:
        """Register DBus objects before the name is acquired."""
        self._connection = connection

        connection.register_object(
            SNI_PATH, self._sni_info,
            self._on_sni_method, self._on_sni_property, None,
        )
        connection.register_object(
            MENU_PATH, self._menu_info,
            self._on_menu_method, self._on_menu_property, None,
        )

    def _on_name_acquired(self, connection: Gio.DBusConnection, name: str) -> None:
        """Register with the StatusNotifierWatcher after bus name is owned."""
        try:
            connection.call_sync(
                WATCHER_BUS, WATCHER_PATH, WATCHER_BUS,
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self._bus_name,)),
                None, Gio.DBusCallFlags.NONE, -1, None,
            )
        except GLib.Error as e:
            print(f"Failed to register tray icon: {e.message}")
            print("Install 'AppIndicator and KStatusNotifierItem Support' GNOME extension.")

    # -- SNI interface --

    def _on_sni_method(self, conn, sender, path, iface, method, params, invocation):
        invocation.return_value(None)

    def _on_sni_property(self, conn, sender, path, iface, prop_name):
        props = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "soupawhisper"),
            "Title": GLib.Variant("s", "SoupaWhisper"),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", self._icon_name),
            "IconThemePath": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "Menu": GLib.Variant("o", MENU_PATH),
            "ItemIsMenu": GLib.Variant("b", True),
        }
        return props.get(prop_name)

    # -- DBusMenu interface --

    def _get_item_properties(self) -> dict[int, dict[str, GLib.Variant]]:
        """Return properties for all menu items, keyed by item ID."""
        return {
            0: {"children-display": _to_variant("submenu")},
            _ID_STATUS: {"label": _to_variant(self._status_label), "enabled": _to_variant(False)},
            _ID_MODEL: {"label": _to_variant(f"Model: {self._model_name}"), "enabled": _to_variant(False)},
            _ID_SEP1: {"type": _to_variant("separator")},
            _ID_SWITCH: {"label": _to_variant("Switch model"), "children-display": _to_variant("submenu")},
            _ID_MODEL_BASE: {
                "label": _to_variant("base"),
                "toggle-type": _to_variant("radio"),
                "toggle-state": _to_variant(1 if self._model_name == "base" else 0),
            },
            _ID_MODEL_SMALL: {
                "label": _to_variant("small"),
                "toggle-type": _to_variant("radio"),
                "toggle-state": _to_variant(1 if self._model_name == "small" else 0),
            },
            _ID_MODEL_MEDIUM: {
                "label": _to_variant("medium"),
                "toggle-type": _to_variant("radio"),
                "toggle-state": _to_variant(1 if self._model_name == "medium" else 0),
            },
            _ID_MODEL_LARGE: {
                "label": _to_variant("large-v3"),
                "toggle-type": _to_variant("radio"),
                "toggle-state": _to_variant(1 if self._model_name == "large-v3" else 0),
            },
            _ID_HISTORY: {"label": _to_variant("History")},
            _ID_SEP2: {"type": _to_variant("separator")},
            _ID_QUIT: {"label": _to_variant("Quit")},
        }

    def _on_menu_method(self, conn, sender, path, iface, method, params, invocation):
        try:
            self._dispatch_menu_method(method, params, invocation)
        except Exception as e:
            print(f"DBusMenu error in {method}: {e}")
            invocation.return_value(None)

    def _dispatch_menu_method(self, method, params, invocation):
        if method == "GetLayout":
            layout = self._build_layout()
            result = GLib.Variant.new_tuple(
                GLib.Variant.new_uint32(self._revision), layout,
            )
            invocation.return_value(result)

        elif method == "Event":
            args = params.unpack()
            item_id, event_id = args[0], args[1]
            if event_id == "clicked":
                self._handle_click(item_id)
            invocation.return_value(None)

        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))

        elif method == "GetGroupProperties":
            args = params.unpack()
            ids, _prop_names = args
            all_props = self._get_item_properties()
            result_items = []
            for item_id in ids:
                props = all_props.get(item_id, {})
                result_items.append((item_id, props))
            invocation.return_value(
                GLib.Variant.new_tuple(GLib.Variant("a(ia{sv})", result_items))
            )

        elif method == "GetProperty":
            args = params.unpack()
            item_id, prop_name = args
            all_props = self._get_item_properties()
            props = all_props.get(item_id, {})
            value = props.get(prop_name, GLib.Variant("s", ""))
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("v", value)))

        elif method == "EventGroup":
            invocation.return_value(GLib.Variant("(ai)", ([],)))

        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))

        else:
            invocation.return_value(None)

    def _on_menu_property(self, conn, sender, path, iface, prop_name):
        props = {
            "Version": GLib.Variant("u", 3),
            "Status": GLib.Variant("s", "normal"),
            "TextDirection": GLib.Variant("s", "ltr"),
        }
        return props.get(prop_name)

    def _build_layout(self) -> GLib.Variant:
        """Build the full menu tree as a DBusMenu layout."""
        model_children = [
            _item(mid, {
                "label": name,
                "toggle-type": "radio",
                "toggle-state": 1 if name == self._model_name else 0,
            }) for mid, name in _MODEL_ACTIONS.items()
        ]

        items = [
            _item(_ID_STATUS, {"label": self._status_label, "enabled": False}),
            _item(_ID_MODEL, {"label": f"Model: {self._model_name}", "enabled": False}),
            _item(_ID_SEP1, {"type": "separator"}),
            _item(_ID_SWITCH, {"label": "Switch model", "children-display": "submenu"}, model_children),
            _item(_ID_HISTORY, {"label": "History"}),
            _item(_ID_SEP2, {"type": "separator"}),
            _item(_ID_QUIT, {"label": "Quit"}),
        ]

        return _item(0, {"children-display": "submenu"}, items)

    def _handle_click(self, item_id: int) -> None:
        """Handle menu item click events."""
        if item_id in _MODEL_ACTIONS:
            threading.Thread(
                target=self.dictation.switch_model,
                args=(_MODEL_ACTIONS[item_id],), daemon=True,
            ).start()
        elif item_id == _ID_HISTORY:
            _open_history_window()
        elif item_id == _ID_QUIT:
            self.dictation.stop()

    # -- Public API --

    def update(self, state: str, model: str | None = None) -> None:
        """Thread-safe tray state update with icon and menu refresh."""
        def _apply():
            icons = {
                "recording": (ICON_RECORDING, "Recording..."),
                "transcribing": (ICON_WORKING, "Transcribing..."),
                "ready": (ICON_READY, "Ready"),
                "loading": (ICON_WORKING, "Loading model..."),
                "error": (ICON_ERROR, "Error"),
            }
            if state in icons:
                self._icon_name, self._status_label = icons[state]
            if model:
                self._model_name = model
            self._revision += 1

            if self._connection:
                self._connection.emit_signal(
                    None, SNI_PATH, SNI_INTERFACE, "NewIcon", None,
                )
                self._connection.emit_signal(
                    None, SNI_PATH, SNI_INTERFACE, "NewStatus",
                    GLib.Variant("(s)", ("Active",)),
                )
                self._connection.emit_signal(
                    None, SNI_PATH,
                    "org.freedesktop.DBus.Properties", "PropertiesChanged",
                    GLib.Variant("(sa{sv}as)", (
                        SNI_INTERFACE,
                        {"IconName": GLib.Variant("s", self._icon_name)},
                        [],
                    )),
                )
                self._connection.emit_signal(
                    None, MENU_PATH, DBUSMENU_INTERFACE, "LayoutUpdated",
                    GLib.Variant("(ui)", (self._revision, 0)),
                )
            return False

        GLib.idle_add(_apply)


# -- History window (GTK, lazy-loaded) --

_Gtk = None


def _ensure_gtk() -> bool:
    """Lazy-load GTK3 for the history window, respecting system theme."""
    global _Gtk
    if _Gtk is not None:
        return True
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        _Gtk = Gtk
        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", _is_dark_theme())
        return True
    except (ImportError, ValueError):
        return False


def _is_dark_theme() -> bool:
    """Check if the system is using a dark theme via GNOME settings."""
    try:
        result = Gio.Settings.new("org.gnome.desktop.interface").get_string("color-scheme")
        return "dark" in result
    except Exception:
        return False


def _open_history_window() -> None:
    """Open the history window if GTK is available."""
    if not _ensure_gtk():
        print("GTK not available — cannot open history window.")
        return
    HistoryWindow().window.show_all()


class HistoryWindow:
    """Window showing transcription history with click-to-copy."""

    def __init__(self) -> None:
        from soupawhisper import history

        self.window = _Gtk.Window(title="SoupaWhisper — History")
        self.window.set_default_size(600, 500)
        self.window.set_position(_Gtk.WindowPosition.CENTER)

        vbox = _Gtk.Box(orientation=_Gtk.Orientation.VERTICAL, spacing=0)
        self.window.add(vbox)

        scrolled = _Gtk.ScrolledWindow()
        scrolled.set_policy(_Gtk.PolicyType.NEVER, _Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        vbox.pack_start(scrolled, True, True, 0)

        self.listbox = _Gtk.ListBox()
        self.listbox.set_selection_mode(_Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self._on_row_activated)
        scrolled.add(self.listbox)

        bottom = _Gtk.Box(spacing=8)
        bottom.set_margin_start(8)
        bottom.set_margin_end(8)
        bottom.set_margin_top(4)
        bottom.set_margin_bottom(4)

        self.count_label = _Gtk.Label()
        self.count_label.set_halign(_Gtk.Align.START)
        bottom.pack_start(self.count_label, True, True, 0)

        self.copy_hint = _Gtk.Label(label="Click a row to copy")
        self.copy_hint.set_halign(_Gtk.Align.END)
        self.copy_hint.set_opacity(0.5)
        bottom.pack_end(self.copy_hint, False, False, 0)

        vbox.pack_end(bottom, False, False, 0)

        entries = history.load()
        for entry in entries:
            self.listbox.add(self._make_row(entry))
        self.count_label.set_text(f"{len(entries)} transcriptions")

    def _make_row(self, entry: dict):
        row = _Gtk.ListBoxRow()
        row._text = entry.get("text", "")

        box = _Gtk.Box(orientation=_Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        timestamp = entry.get("timestamp", "")
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp)
            ts_display = dt.strftime("%d/%m/%Y %H:%M")
        except (ValueError, TypeError):
            ts_display = timestamp

        duration = entry.get("duration", 0)
        model_name = entry.get("model", "?")
        header = _Gtk.Label(label=f"{ts_display}   {duration}s   {model_name}")
        header.set_halign(_Gtk.Align.START)
        header.set_opacity(0.6)
        box.pack_start(header, False, False, 0)

        text = entry.get("text", "")
        display = text[:200] + "..." if len(text) > 200 else text
        label = _Gtk.Label(label=display)
        label.set_halign(_Gtk.Align.START)
        label.set_line_wrap(True)
        label.set_max_width_chars(70)
        label.set_selectable(False)
        box.pack_start(label, False, False, 0)

        row.add(box)
        return row

    def _on_row_activated(self, _listbox, row) -> None:
        text = getattr(row, "_text", "")
        if not text:
            return

        proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
        proc.communicate(input=text.encode("utf-8"))

        self.copy_hint.set_text("Copied!")
        GLib.timeout_add(
            2000, lambda: self.copy_hint.set_text("Click a row to copy") or False,
        )
