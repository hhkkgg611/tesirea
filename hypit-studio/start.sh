#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/uvicorn ]]; then
  echo 'Run bash setup.sh first.' >&2
  exit 1
fi
exec .venv/bin/uvicorn backend.server:app --host 127.0.0.1 --port "${HYPIT_PORT:-8081}" --no-access-log
