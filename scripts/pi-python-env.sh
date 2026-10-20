# shellcheck shell=bash
# One interpreter for start-kiosk.sh and start-tester.sh on the Pi (13 Oct).
#
# picamera2 comes from Raspberry Pi OS, built against the platform libcamera, so the
# camera venv must be the system Python with system site packages. On 13 Oct the boot
# service (start-kiosk.sh without camera arguments) crash-looped, and every loop's
# `uv sync` rebuilt .venv for .python-version (3.11) under the tester's Python 3.13
# venv, which broke the tester's start. The rule here: once the venv sees system site
# packages, or the system Python has picamera2, every start uses that Python and never
# rebuilds the venv for another one.
#
# Source this file from the repository root; it defines functions only.

OPENFLIGHT_SYSTEM_PYTHON="${OPENFLIGHT_SYSTEM_PYTHON:-/usr/bin/python3}"

# True while the boot service owns (or is starting) the hardware.
openflight_service_running() {
    command -v systemctl >/dev/null 2>&1 || return 1
    local state
    state="$(systemctl is-active openflight.service 2>/dev/null || true)"
    case "$state" in
        active | activating | reloading) return 0 ;;
        *) return 1 ;;
    esac
}

# The tester never races the kiosk service for the camera, the radars or the venv.
refuse_while_kiosk_service_runs() {
    if openflight_service_running; then
        echo "The OpenFlight kiosk service (openflight.service) is running or starting." >&2
        echo "It owns the camera and radars and rebuilds the Python environment." >&2
        echo "Stop it first, then start the tester again:" >&2
        echo "  sudo systemctl stop openflight.service" >&2
        echo "On a unit used with the tester, also keep it from starting at boot" >&2
        echo "(this stops it now as well):" >&2
        echo "  sudo systemctl disable --now openflight.service" >&2
        return 1
    fi
    return 0
}

venv_uses_system_site_packages() {
    [ -f .venv/pyvenv.cfg ] \
        && grep -Eiq '^[[:space:]]*include-system-site-packages[[:space:]]*=[[:space:]]*true' \
            .venv/pyvenv.cfg
}

system_python_has_picamera2() {
    [ -x "$OPENFLIGHT_SYSTEM_PYTHON" ] \
        && "$OPENFLIGHT_SYSTEM_PYTHON" -c 'import picamera2' >/dev/null 2>&1
}

# Pin uv to the system Python when this is (or has been) a camera unit.
use_pi_system_python() {
    if venv_uses_system_site_packages || system_python_has_picamera2; then
        export UV_PYTHON="$OPENFLIGHT_SYSTEM_PYTHON"
        return 0
    fi
    return 1
}

# Build the system-Python venv only when it is missing or is not that venv.
ensure_system_python_venv() {
    export UV_PYTHON="$OPENFLIGHT_SYSTEM_PYTHON"
    if [ -x .venv/bin/python ] && venv_uses_system_site_packages; then
        if ! system_python_has_picamera2 || .venv/bin/python -c 'import picamera2' >/dev/null 2>&1; then
            return 0
        fi
    fi
    uv venv --clear --system-site-packages --python "$OPENFLIGHT_SYSTEM_PYTHON"
}
