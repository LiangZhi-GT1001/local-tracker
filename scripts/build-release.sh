#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${LOCAL_TRACKER_RELEASE_DIR:-$ROOT_DIR/target}"

python3 "$ROOT_DIR/scripts/build-release.py" --output-dir "$OUTPUT_DIR"
