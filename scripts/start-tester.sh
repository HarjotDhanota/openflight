#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it before running the tester capture." >&2
  exit 1
fi

# picamera2 is installed by Raspberry Pi OS against the platform libcamera, so
# the venv has to see system packages. The tester's saved-capture comparison
# also uses OpenCV from the camera extra.
export UV_PYTHON=/usr/bin/python3
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import picamera2" >/dev/null 2>&1; then
  uv venv --clear --system-site-packages --python /usr/bin/python3
fi
# lgpio (Pi 5 GPIO) builds from source and needs SWIG and its C header.
if ! ls .venv/lib/python3*/site-packages/lgpio* >/dev/null 2>&1 \
  && { ! command -v swig >/dev/null 2>&1 || [ ! -f /usr/include/lgpio.h ]; }; then
  echo "Missing build tools for lgpio. Run once, then start again:" >&2
  echo "  sudo apt update && sudo apt install -y swig liblgpio-dev python3-dev" >&2
  exit 1
fi
uv sync --extra camera

exec uv run --extra camera python -m openflight.camera.tester_server "$@"
