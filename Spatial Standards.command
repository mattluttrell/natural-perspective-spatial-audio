#!/usr/bin/env bash
# Double-click this in Finder (macOS) to install on first run and launch the GUI
# every time. It opens a Terminal window; that's expected.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/spatial-standards-gui ]; then
  ./install.sh || { echo; echo "Install failed — scroll up for the reason."; \
                    read -n1 -s -r -p "Press any key to close."; exit 1; }
fi
./gui
