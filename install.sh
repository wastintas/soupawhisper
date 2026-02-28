#!/usr/bin/env bash
set -euo pipefail

APP_NAME="soupawhisper"
SERVICE_NAME="soupawhisper"
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== SoupaWhisper Installer ==="

# -- Detect package manager --

install_packages() {
    if command -v dnf &>/dev/null; then
        echo "Detected Fedora/RHEL (dnf)"
        sudo dnf install -y alsa-utils wl-clipboard ydotool libnotify
    elif command -v apt &>/dev/null; then
        echo "Detected Debian/Ubuntu (apt)"
        sudo apt install -y alsa-utils wl-clipboard ydotool libnotify-bin
    elif command -v pacman &>/dev/null; then
        echo "Detected Arch (pacman)"
        sudo pacman -S --noconfirm alsa-utils wl-clipboard ydotool libnotify
    else
        echo "Unsupported package manager. Install manually:"
        echo "  alsa-utils, wl-clipboard, ydotool, libnotify"
        exit 1
    fi
}

# -- Install Python dependencies --

install_python_deps() {
    if command -v poetry &>/dev/null; then
        echo "Installing Python dependencies with Poetry..."
        cd "$INSTALL_DIR"
        poetry install
    else
        echo "Poetry not found. Install it: https://python-poetry.org/docs/#installation"
        exit 1
    fi
}

# -- Setup input group --

setup_input_group() {
    if groups "$USER" | grep -q '\binput\b'; then
        echo "User already in 'input' group."
    else
        echo "Adding $USER to 'input' group..."
        sudo usermod -aG input "$USER"
        echo "NOTE: Log out and back in for group changes to take effect."
    fi
}

# -- Setup udev rules for /dev/uinput --

setup_udev() {
    local rule_file="/etc/udev/rules.d/80-uinput.rules"
    if [ -f "$rule_file" ]; then
        echo "udev rule already exists."
    else
        echo "Creating udev rule for /dev/uinput..."
        echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee "$rule_file"
        sudo udevadm control --reload-rules
        sudo udevadm trigger
    fi
}

# -- Setup systemd service --

setup_systemd() {
    local service_dir="$HOME/.config/systemd/user"
    local service_file="$service_dir/$SERVICE_NAME.service"

    mkdir -p "$service_dir"

    cat > "$service_file" << EOF
[Unit]
Description=SoupaWhisper Voice Dictation
After=graphical-session.target

[Service]
Type=simple
ExecStart=$(command -v poetry) -C $INSTALL_DIR run python -m soupawhisper
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    echo "Systemd service installed: $SERVICE_NAME"
    echo "  Start: systemctl --user start $SERVICE_NAME"
    echo "  Logs:  journalctl --user -u $SERVICE_NAME -f"
}

# -- Main --

install_packages
install_python_deps
setup_input_group
setup_udev
setup_systemd

echo ""
echo "=== Installation complete ==="
echo "Start with: systemctl --user start $SERVICE_NAME"
echo "Or run directly: poetry run python -m soupawhisper"
