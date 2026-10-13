"""Contract tests for the tester start script and the Pi interpreter rule (13 Oct).

On the Pi, ``openflight.service`` (start-kiosk.sh without camera arguments) crash-looped
and every loop ran ``uv sync``, which rebuilt ``.venv`` for ``.python-version`` 3.11 while
start-tester.sh had built a Python 3.13 venv with system site packages for picamera2. The
two scripts now share one rule (scripts/pi-python-env.sh): when the venv already sees
system site packages, or the system Python has picamera2, both use /usr/bin/python3,
and the tester refuses to race a running kiosk service.
"""

import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="start-script contract tests need bash",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts/pi-python-env.sh"
TESTER = REPO_ROOT / "scripts/start-tester.sh"
KIOSK = REPO_ROOT / "scripts/start-kiosk.sh"


def _bash_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name != "nt":
        return resolved
    # `bash` on Windows may be WSL's (System32 comes first), which wants /mnt/c paths
    converted = subprocess.run(
        ["bash", "-lc", f"wslpath -u {shlex.quote(resolved.replace(chr(92), '/'))}"],
        capture_output=True,
        text=True,
        check=False,
    )
    mapped = converted.stdout.strip()
    if converted.returncode == 0 and mapped:
        return mapped
    posix = Path(resolved).as_posix()
    return f"/{posix[0].lower()}{posix[2:]}"


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run(tmp_path: Path, body: str, *, service_state: str = "inactive", system_python=None):
    """Source the helper in ``tmp_path`` with a stub systemctl and system Python."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_executable(
        bin_dir / "systemctl",
        "#!/usr/bin/env bash\n"
        f'if [ "$1" = "is-active" ]; then echo {service_state}; '
        f'[ "{service_state}" = active ]; exit $?; fi\nexit 0\n',
    )
    _write_executable(
        bin_dir / "uv",
        f'#!/usr/bin/env bash\nprintf "uv %s\\n" "$*" >> {_bash_path(tmp_path / "uv.log")}\n',
    )
    python = tmp_path / "system-python"
    _write_executable(
        python,
        "#!/usr/bin/env bash\n" + ("exit 0\n" if system_python == "picamera2" else "exit 1\n"),
    )
    script = (
        f"set -euo pipefail\ncd {_bash_path(tmp_path)}\n"
        f'export PATH="{_bash_path(bin_dir)}:$PATH"\n'
        f"export OPENFLIGHT_SYSTEM_PYTHON={_bash_path(python)}\n"
        f"source {_bash_path(HELPER)}\n{body}"
    )
    # a file, not ``bash -c``: WSL's launcher expands $VARS in a -c argument too early
    runner = tmp_path / "run.sh"
    runner.write_text(script + "\n", encoding="utf-8", newline="\n")
    return subprocess.run(["bash", _bash_path(runner)], capture_output=True, text=True, check=False)


def _venv(tmp_path: Path, *, system_site: bool) -> None:
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text(
        "home = /usr/bin\n"
        f"include-system-site-packages = {'true' if system_site else 'false'}\n"
        "version_info = 3.13.5\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("state", ["active", "activating"])
def test_service_running_is_detected_for_active_and_activating(tmp_path, state):
    result = _run(
        tmp_path,
        "if openflight_service_running; then echo running; else echo idle; fi",
        service_state=state,
    )

    assert result.stdout.strip() == "running", result.stderr


def test_an_inactive_service_is_not_running(tmp_path):
    result = _run(tmp_path, "if openflight_service_running; then echo running; else echo idle; fi")

    assert result.stdout.strip() == "idle", result.stderr


def test_tester_refuses_to_race_the_kiosk_service_with_the_exact_command(tmp_path):
    result = _run(tmp_path, "refuse_while_kiosk_service_runs", service_state="activating")

    assert result.returncode != 0
    assert "sudo systemctl stop openflight.service" in result.stderr


def test_a_venv_with_system_site_packages_pins_the_system_python(tmp_path):
    _venv(tmp_path, system_site=True)

    result = _run(tmp_path, 'use_pi_system_python && echo "$UV_PYTHON"')

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("system-python")


def test_a_system_python_with_picamera2_pins_it_even_without_a_venv(tmp_path):
    result = _run(tmp_path, 'use_pi_system_python && echo "$UV_PYTHON"', system_python="picamera2")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("system-python")


def test_a_desktop_without_picamera2_keeps_the_project_python(tmp_path):
    result = _run(tmp_path, "if use_pi_system_python; then echo pinned; else echo project; fi")

    assert result.stdout.strip() == "project", result.stderr


def test_an_existing_system_site_venv_is_never_rebuilt(tmp_path):
    _venv(tmp_path, system_site=True)
    _write_executable(tmp_path / ".venv" / "python-stub", "#!/usr/bin/env bash\nexit 0\n")
    (tmp_path / ".venv" / "bin").mkdir()
    _write_executable(tmp_path / ".venv" / "bin" / "python", "#!/usr/bin/env bash\nexit 0\n")

    result = _run(tmp_path, "ensure_system_python_venv", system_python="picamera2")

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "uv.log").exists()


def test_a_venv_without_system_site_packages_is_rebuilt_on_the_system_python(tmp_path):
    _venv(tmp_path, system_site=False)
    (tmp_path / ".venv" / "bin").mkdir()
    _write_executable(tmp_path / ".venv" / "bin" / "python", "#!/usr/bin/env bash\nexit 0\n")

    result = _run(tmp_path, "ensure_system_python_venv", system_python="picamera2")

    assert result.returncode == 0, result.stderr
    log = (tmp_path / "uv.log").read_text(encoding="utf-8")
    assert "venv --clear --system-site-packages --python" in log
    assert "system-python" in log


def test_both_start_scripts_source_the_shared_rule():
    for script in (TESTER, KIOSK):
        text = script.read_text(encoding="utf-8")
        assert 'source "$SCRIPT_DIR/pi-python-env.sh"' in text, script.name


def test_the_tester_checks_the_service_before_touching_the_venv():
    text = TESTER.read_text(encoding="utf-8")

    assert text.index("refuse_while_kiosk_service_runs") < text.index("ensure_system_python_venv")
    assert text.index("ensure_system_python_venv") < text.index("uv sync")


def test_a_kiosk_start_on_a_system_python_venv_never_rebuilds_or_strips_it():
    text = KIOSK.read_text(encoding="utf-8")
    block = text[text.index("UV_SYNC_ARGS=(--quiet)") : text.index("\nconfigure_kld7_latency\n")]

    assert "if has_camera_arg || use_pi_system_python; then" in block
    assert "ensure_system_python_venv" in block
    # the tester's camera packages stay installed when the kiosk syncs the shared venv
    assert "UV_SYNC_ARGS+=(--inexact)" in block


def test_helper_has_valid_shell_syntax_and_unix_newlines():
    assert b"\r\n" not in HELPER.read_bytes()
    subprocess.run(["bash", "-n", shlex.quote(_bash_path(HELPER))], check=True)
