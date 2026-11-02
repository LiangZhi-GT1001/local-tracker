#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_DIR="$ROOT_DIR/packages/cli"
SKILL_DIR="$ROOT_DIR/packages/skill"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
DATA_HOME="${LOCAL_TRACKER_DATA_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/local-tracker}"
BIN_DIR="${LOCAL_TRACKER_BIN_DIR:-$HOME/.local/bin}"
VENV_DIR="$DATA_HOME/venv"

python3 -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install "$CLI_DIR"

mkdir -p "$BIN_DIR"
ln -sfn "$VENV_DIR/bin/lt" "$BIN_DIR/lt"

install_skill() {
  local destination="$1/skills/local-tracker-skill"
  python3 - "$SKILL_DIR" "$destination" <<'PY'
from pathlib import Path
import os
import shutil
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
staging = destination.with_name(f".{destination.name}.installing")

if staging.is_symlink() or staging.is_file():
    staging.unlink()
elif staging.is_dir():
    shutil.rmtree(staging)

staging.mkdir(parents=True)
shutil.copy2(source / "SKILL.md", staging / "SKILL.md")
for name in ("agents", "assets", "scripts"):
    shutil.copytree(
        source / name,
        staging / name,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )

destination.parent.mkdir(parents=True, exist_ok=True)
if destination.is_symlink() or destination.is_file():
    destination.unlink()
elif destination.is_dir():
    shutil.rmtree(destination)
os.replace(staging, destination)
PY
  echo "local-tracker-skill installed to $destination"
}

install_skill "$CODEX_HOME"
install_skill "$CLAUDE_HOME"

echo "local-tracker CLI installed to $VENV_DIR"
echo "Command link: $BIN_DIR/lt"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo "Add $BIN_DIR to PATH before using lt from a new terminal."
elif [[ "$(command -v lt 2>/dev/null || true)" != "$BIN_DIR/lt" ]]; then
  echo "Another lt command appears earlier on PATH; move $BIN_DIR before it."
fi
echo "Restart Codex and Claude Code to load the updated Skill."
echo "Try: lt --help"
