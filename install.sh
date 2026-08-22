#!/usr/bin/env bash
# Install `tunnels` on PATH. Symlinks, so a git pull updates the command.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUD_PYTHON=/usr/bin/python3   # the interpreter that carries pyobjc

# Pick the first writable directory that is already on PATH.
BIN=""
for candidate in "$HOME/.local/bin" "$HOME/bin" /opt/homebrew/bin /usr/local/bin; do
  case ":$PATH:" in *":$candidate:"*) [ -w "$candidate" ] && BIN="$candidate" && break ;; esac
done
if [ -z "$BIN" ]; then
  echo "no writable directory on PATH. Make one and add it:" >&2
  echo "  mkdir -p ~/.local/bin && echo 'export PATH=\$HOME/.local/bin:\$PATH' >> ~/.zshrc" >&2
  exit 1
fi

echo "==> checking what is missing"
missing=0
for tool in aws session-manager-plugin kubectl; do
  if command -v "$tool" >/dev/null; then
    echo "    ok       $tool"
  else
    echo "    MISSING  $tool"
    missing=1
  fi
done

python3 -c "import yaml" 2>/dev/null \
  && echo "    ok       pyyaml" \
  || { echo "    MISSING  pyyaml (pip install pyyaml)"; missing=1; }

if "$HUD_PYTHON" -c "import Cocoa" 2>/dev/null; then
  echo "    ok       pyobjc (floating label)"
else
  echo "==> installing pyobjc for the floating label"
  "$HUD_PYTHON" -m pip install --user --quiet pyobjc-framework-Cocoa
fi

[ "$missing" = 1 ] && echo "install the MISSING tools above, then run this again" >&2 && exit 1

echo "==> linking"
ln -sf "$REPO/tunnels" "$BIN/tunnels"
echo "    $BIN/tunnels -> $REPO/tunnels"

CONFIG="$HOME/.config/tunnels/config.yaml"
if [ -f "$CONFIG" ]; then
  echo "    config already at $CONFIG, left alone"
else
  mkdir -p "$(dirname "$CONFIG")"
  cp "$REPO/config.example.yaml" "$CONFIG"
  echo "    seeded $CONFIG - edit it before the first run"
fi

echo
echo "done. try:  tunnels status"
