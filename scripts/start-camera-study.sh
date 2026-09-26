#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it before running the camera study." >&2
  exit 1
fi

export UV_PYTHON=/usr/bin/python3
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import picamera2" >/dev/null 2>&1; then
  uv venv --clear --system-site-packages --python /usr/bin/python3
fi
uv sync --quiet --extra camera

exec uv run python -m openflight.camera.study_server "$@"
