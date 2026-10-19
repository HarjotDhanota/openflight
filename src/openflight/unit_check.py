"""The tester's unit check: is every sensor of a four-sensor unit present and answering?

The tester test is only for units with all four sensors: the OPS243 speed radar,
the IWR6843 ranging radar, the OV9281 camera and the LIS3DH tilt sensor. This
checks the first three from their own ports (the tester server reads the LIS3DH
itself, all the time) and writes one small JSON result the tester server reads.
It exits 0 only when all three answered.

Each check reports in plain words what it found and, when it failed, what to do.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

SCHEMA = "openflight.unit_check.v1"
RESULT_FILE = "unit-check.json"
SENSORS = ("ops243", "camera", "iwr6843")
# what the page calls each sensor; LIS3DH is checked by the tester server itself
SENSOR_NAMES = {
    "ops243": "Speed radar (OPS243)",
    "camera": "Camera (OV9281)",
    "iwr6843": "Ranging radar (IWR6843)",
    "lis3dh": "Tilt sensor (LIS3DH)",
}
REMEDIES = {
    "ops243": "Check the speed radar's cable and power, then check the unit again.",
    "camera": "Check the camera's ribbon cable at both ends, then check the unit again.",
    "iwr6843": (
        "Check the ranging radar's USB cable. If it still fails, press RESET on the "
        "radar board, tick the RESET box and check the unit again."
    ),
}
REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_CLI = REPO_ROOT / "scripts" / "iwr6843" / "check_cli.py"
CAMERA_LINE = re.compile(r"^\s*\d+\s*:\s*(\S+)")
COMMAND_TIMEOUT_S = 60.0


def _result(sensor: str, ok: bool, detail: str) -> dict[str, Any]:
    return {
        "id": sensor,
        "name": SENSOR_NAMES[sensor],
        "ok": bool(ok),
        "detail": detail,
        "remedy": None if ok else REMEDIES[sensor],
    }


def _run(command: Sequence[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return 127, f"{type(error).__name__}: {error}"
    return done.returncode, f"{done.stdout}\n{done.stderr}"


def check_camera(run: Callable[[Sequence[str]], tuple[int, str]] = _run) -> dict[str, Any]:
    """The camera answers when libcamera lists at least one sensor."""
    code, output = run(["rpicam-hello", "--list-cameras"])
    names = [match.group(1) for line in output.splitlines() if (match := CAMERA_LINE.match(line))]
    if names:
        return _result("camera", True, f"found {', '.join(names)}")
    reason = "no camera listed" if code == 0 else f"camera listing failed ({code})"
    last = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
    return _result("camera", False, f"{reason}{f': {last}' if last else ''}")


def _probe_ops(port: str | None) -> str | None:
    from openflight.ops243 import OPS243Radar  # pylint: disable=import-outside-toplevel

    radar = OPS243Radar(port=port)
    try:
        radar.connect()
        # pylint: disable-next=protected-access
        return radar._probe_firmware_version()
    finally:
        radar.disconnect()


def check_ops(
    port: str | None, probe: Callable[[str | None], str | None] = _probe_ops
) -> dict[str, Any]:
    """The speed radar answers when it reports its firmware version."""
    try:
        version = probe(port)
    except Exception as error:  # pylint: disable=broad-exception-caught
        return _result("ops243", False, f"no answer on {port or 'USB'}: {error}")
    if not version:
        return _result("ops243", False, f"no firmware reply on {port or 'USB'}")
    return _result("ops243", True, f"firmware {version} on {port or 'USB'}")


def check_iwr(
    port: str | None,
    operator_reset: str | None,
    run: Callable[[Sequence[str]], tuple[int, str]] = _run,
) -> dict[str, Any]:
    """The ranging radar answers when its CLI replies (scripts/iwr6843/check_cli.py)."""
    command = [sys.executable, str(CHECK_CLI)]
    if port:
        command += ["--port", port]
    if operator_reset:
        command += ["--operator-reset", operator_reset]
    code, output = run(command)
    # its evidence line and its one reason line go to the job log as they are
    print(output.strip(), flush=True)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    last = next(
        (line for line in reversed(lines) if not line.startswith("IWR6843 CLI evidence")), ""
    )
    return _result("iwr6843", code == 0, last or ("ready" if code == 0 else f"exit {code}"))


def run_checks(
    *,
    ops_port: str | None,
    iwr_port: str | None,
    operator_reset: str | None,
    camera: Callable[[], dict] | None = None,
    ops: Callable[[str | None], dict] | None = None,
    iwr: Callable[[str | None, str | None], dict] | None = None,
) -> dict[str, Any]:
    results = [
        (ops or check_ops)(ops_port),
        (camera or check_camera)(),
        (iwr or check_iwr)(iwr_port, operator_reset),
    ]
    return {
        "schema": SCHEMA,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "operator_reset": operator_reset,
        "sensors": {item["id"]: item for item in results},
        "all_ok": all(item["ok"] for item in results),
    }


def read_result(path: Path) -> dict[str, Any] | None:
    """A result this check wrote, or None."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        return None
    sensors = document.get("sensors")
    if not isinstance(sensors, dict) or set(sensors) != set(SENSORS):
        return None
    return document


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ops-port", default=None, help="OPS243 serial port (USB when omitted)")
    parser.add_argument("--iwr-port", default=None, help="IWR6843 CLI port (auto when omitted)")
    parser.add_argument("--operator-reset", choices=("pressed", "not-pressed"), default=None)
    parser.add_argument("--out", type=Path, required=True, help="Where to write the result")
    args = parser.parse_args(argv)
    result = run_checks(
        ops_port=args.ops_port, iwr_port=args.iwr_port, operator_reset=args.operator_reset
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(f".{args.out.name}.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.out)
    for item in result["sensors"].values():
        print(f"{item['name']}: {'ready' if item['ok'] else 'NOT READY'} — {item['detail']}")
    return 0 if result["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
