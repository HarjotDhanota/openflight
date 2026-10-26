#!/usr/bin/env bash
# Start the tester server. Its page opens on a phone or laptop
# (http://<pi address>:8765/), or, with --on-screen (or OPENFLIGHT_TESTER_ON_SCREEN=1),
# fullscreen on the unit's own screen in the kiosk's Electron shell: that one
# window then switches between the tester page and the kiosk (Kiosk view and the
# kiosk's Tester button), and free play started there never opens a second one.
# Every other option goes to the tester server unchanged.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# the kiosk's web port: the tester server starts every kiosk on it (KIOSK_WEB_PORT)
KIOSK_WEB_PORT=8080
DRY_RUN=false
TESTER_PORT=8765
TESTER_HOST=127.0.0.1
SERVER_ARGS=()
case "${OPENFLIGHT_TESTER_ON_SCREEN:-}" in
    1|true|yes) ON_SCREEN=true ;;
    *) ON_SCREEN=false ;;
esac

log() { printf '[OpenFlight tester] %s\n' "$1"; }
warn() { printf '[OpenFlight tester] WARNING: %s\n' "$1" >&2; }
error() { printf '[OpenFlight tester] ERROR: %s\n' "$1" >&2; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --on-screen) ON_SCREEN=true ;;
        --dry-run) DRY_RUN=true ;;
        --port|--host)
            if [ "$#" -lt 2 ]; then
                error "$1 requires a value"
                exit 2
            fi
            [ "$1" = --port ] && TESTER_PORT="$2" || TESTER_HOST="$2"
            SERVER_ARGS+=("$1" "$2")
            shift
            ;;
        --port=*) TESTER_PORT="${1#--port=}"; SERVER_ARGS+=("$1") ;;
        --host=*) TESTER_HOST="${1#--host=}"; SERVER_ARGS+=("$1") ;;
        *) SERVER_ARGS+=("$1") ;;
    esac
    shift
done

SERVER_CMD=(uv run --extra camera python -m openflight.camera.tester_server "${SERVER_ARGS[@]}")
# The shell reaches the page through loopback, so the server must listen there.
TESTER_URL="http://localhost:$TESTER_PORT/"
TESTER_ORIGINS="http://localhost:$TESTER_PORT http://localhost:$KIOSK_WEB_PORT"
if [ "$ON_SCREEN" = true ]; then
    case "$TESTER_HOST" in
        127.0.0.1|localhost|0.0.0.0|::) ;;
        *)
            error "--on-screen needs --host 127.0.0.1 or 0.0.0.0: the unit's screen opens $TESTER_URL"
            exit 2
            ;;
    esac
fi

if [ "$DRY_RUN" = true ]; then
    printf '%q ' "${SERVER_CMD[@]}"
    printf '\n'
    if [ "$ON_SCREEN" = true ]; then
        printf 'tester shell: %s\n' "$TESTER_URL" >&2
        printf 'tester shell origins: %s\n' "$TESTER_ORIGINS" >&2
    else
        printf 'tester shell: off\n' >&2
    fi
    exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it before running the tester capture." >&2
  exit 1
fi

# shellcheck source=pi-python-env.sh
source "$SCRIPT_DIR/pi-python-env.sh"

# The boot service owns the camera and radars, and its starts sync the venv: never
# race it (13 Oct).
refuse_while_kiosk_service_runs || exit 1

# picamera2 is installed by Raspberry Pi OS against the platform libcamera, so
# the venv has to see system packages. The tester's saved-capture comparison
# also uses OpenCV from the camera extra.
ensure_system_python_venv
# lgpio (Pi 5 GPIO) builds from source and needs SWIG and its C header.
if ! ls .venv/lib/python3*/site-packages/lgpio* >/dev/null 2>&1 \
  && { ! command -v swig >/dev/null 2>&1 || [ ! -f /usr/include/lgpio.h ]; }; then
  echo "Missing build tools for lgpio. Run once, then start again:" >&2
  echo "  sudo apt update && sudo apt install -y swig liblgpio-dev python3-dev" >&2
  exit 1
fi
uv sync --extra camera

if [ "$ON_SCREEN" != true ]; then
    exec "${SERVER_CMD[@]}"
fi

# --- the tester page on the unit's own screen ---------------------------------
SERVER_PID=""
BROWSER_PID=""
BROWSER_PGID=""
BROWSER_LAUNCHED=false
# shellcheck source=kiosk-browser.sh
source "$SCRIPT_DIR/kiosk-browser.sh"

# ensure-kiosk-ui.sh reports a failed build through this (start-kiosk.sh's splash has its own)
show_startup_failure() {
    error "$2"
    error "$3"
    exit 1
}

tester_answers() {
    curl -fsS --max-time 1 "http://127.0.0.1:$TESTER_PORT/" >/dev/null 2>&1
}

stop_on_screen() {
    trap - EXIT SIGINT SIGTERM
    stop_kiosk_browser
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}

run_on_screen() {
    # the kiosk UI and its Electron shell: free play's kiosk needs the UI anyway
    # shellcheck source=ensure-kiosk-ui.sh
    source "$SCRIPT_DIR/ensure-kiosk-ui.sh"
    ensure_kiosk_ui
    trap stop_on_screen EXIT
    trap 'stop_on_screen; exit 130' SIGINT SIGTERM
    "${SERVER_CMD[@]}" &
    SERVER_PID=$!
    log "Waiting for the tester page..."
    local _
    for _ in {1..120}; do
        tester_answers && break
        kill -0 "$SERVER_PID" 2>/dev/null || break
        sleep 0.5
    done
    if ! tester_answers; then
        error "The tester server did not start; see the lines above."
        exit 1
    fi
    launch_kiosk_browser "$TESTER_URL" "$TESTER_ORIGINS" \
        || warn "No kiosk shell: open $TESTER_URL in a browser on this screen."
    log "The tester page is on this screen. Press Ctrl+C here to stop."
    wait "$SERVER_PID"
}

run_on_screen
