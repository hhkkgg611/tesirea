#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
for tool in python3 ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool (install Python 3.10+ and FFmpeg)." >&2; exit 1; }
done
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.lock
