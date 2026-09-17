#!/usr/bin/env bash
# Creates an app-local virtual environment. System packages are installed only
# when explicitly requested; sudo uses the terminal's standard password prompt.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ "${1:-}" == --system-deps ]]; then
  if command -v apt-get >/dev/null; then
    sudo apt-get update
    sudo apt-get install -y python3-venv git libegl1 libopengl0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxkbcommon-x11-0 libxcb-xinerama0 libxcb-render-util0 fonts-noto-core fonts-noto-cjk gnome-keyring libsecret-1-0 policykit-1
  elif command -v dnf >/dev/null; then
    sudo dnf install -y python3 python3-pip git mesa-libEGL libglvnd-opengl xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image xcb-util-renderutil libxkbcommon-x11 google-noto-sans-fonts gnome-keyring polkit
  else
    printf '%s\n' 'Install Python 3.12+, Git, Qt/XCB runtime libraries, and a Secret Service keyring using your distro package manager. See README.md.' >&2
    exit 1
  fi
elif [[ -n "${1:-}" ]]; then
  printf '%s\n' 'Usage: bash tools/setup-linux.sh [--system-deps]' >&2
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12 or newer is required"'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
printf '%s\n' 'Ready. Launch with: bash run.sh (or bash run.sh --demo).'
