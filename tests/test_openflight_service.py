"""Contracts for the boot-time systemd unit."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UNIT = REPO_ROOT / "scripts/setup/openflight.service"
SETUP = REPO_ROOT / "scripts/setup/setup.sh"


def _unit_lines() -> list[str]:
    return [line.strip() for line in UNIT.read_text(encoding="utf-8").splitlines()]


def _section(name: str) -> list[str]:
    lines = _unit_lines()
    start = lines.index(f"[{name}]") + 1
    body = []
    for line in lines[start:]:
        if line.startswith("["):
            break
        if line:
            body.append(line)
    return body


def test_service_does_not_restart_when_another_instance_owns_the_kiosk():
    """Exit 3 means "someone else is running OpenFlight"; retrying every 5 s is noise."""
    assert "RestartPreventExitStatus=3" in _section("Service")


def test_service_stops_crash_looping_instead_of_retrying_forever():
    """1,100 restarts in 90 minutes each ran cleanup against the desktop session's kiosk."""
    unit = _section("Unit")
    burst = next(line for line in unit if line.startswith("StartLimitBurst="))
    interval = next(line for line in unit if line.startswith("StartLimitIntervalSec="))

    assert int(burst.split("=", 1)[1]) <= 5
    assert int(interval.split("=", 1)[1]) >= 60


def test_every_home_path_in_the_unit_is_rewritten_by_setup():
    """setup.sh only rewrites the project path; any other /home/coleman entry would ship stale."""
    setup = SETUP.read_text(encoding="utf-8")
    assert "s|/home/coleman/openflight|$PROJECT_DIR|g" in setup

    for line in _unit_lines():
        if "/home/coleman" in line:
            assert "/home/coleman/openflight" in line, line


def _installed_unit(user: str, project_dir: str) -> str:
    """The unit text exactly as setup.sh writes it to /etc/systemd/system."""
    setup = SETUP.read_text(encoding="utf-8")
    match = re.search(
        r"(sed -e \"s\|\^User=.*?\"\$SCRIPT_DIR/openflight\.service\")"
        r"\s*\|\s*sudo tee /etc/systemd/system/openflight\.service",
        setup,
        flags=re.DOTALL,
    )
    assert match, "setup.sh no longer installs the unit from scripts/setup/openflight.service"
    script = (
        f"USER={shlex.quote(user)}\n"
        f"PROJECT_DIR={shlex.quote(project_dir)}\n"
        "SCRIPT_DIR=scripts/setup\n"
        f"{match.group(1)}\n"
    )
    # stdin, not ``bash -c``: WSL's launcher expands $VARS in a -c argument too early.
    # Bytes, so Windows text mode cannot turn the newlines into CRLF.
    result = subprocess.run(
        ["bash", "-s"],
        input=script.encode("utf-8"),
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8")


@pytest.mark.skipif(shutil.which("bash") is None, reason="renders the unit with setup.sh's sed")
def test_the_installed_unit_carries_the_restart_limits():
    """98 restarts in 10 minutes came from a Pi whose installed unit predated the limits."""
    installed = _installed_unit("pi", "/home/pi/openflight")
    lines = [line.strip() for line in installed.splitlines()]
    unit = lines[lines.index("[Unit]") + 1 : lines.index("[Service]")]
    service = lines[lines.index("[Service]") + 1 : lines.index("[Install]")]

    assert "StartLimitIntervalSec=300" in unit
    assert "StartLimitBurst=5" in unit
    assert "Restart=on-failure" in service
    assert "RestartPreventExitStatus=3" in service
    assert "User=pi" in service
    assert "ExecStart=/home/pi/openflight/scripts/start-kiosk.sh" in service
    assert "coleman" not in installed
