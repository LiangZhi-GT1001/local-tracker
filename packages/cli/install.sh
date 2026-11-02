#!/usr/bin/env bash
set -euo pipefail

echo "Installing local-tracker..."

# Check Python version
python3 -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required'" 2>/dev/null || {
    echo "Error: Python 3.10+ is required"
    exit 1
}

# Install from source
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
pip3 install -e "$SCRIPT_DIR" 2>/dev/null || pip install -e "$SCRIPT_DIR"

# Verify
if command -v lt &>/dev/null; then
    echo "✓ local-tracker installed successfully"
    echo "  Try: lt sessions"
    echo "       lt usage --period daily"
    echo "       lt capabilities"
else
    echo "⚠ 'lt' command not found in PATH"
    echo "  Make sure your Python bin directory is in PATH"
    echo "  Or run: python3 -m src.cli"
fi
