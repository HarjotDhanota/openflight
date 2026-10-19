"""The tester's unit check: each of the four sensors present and answering."""

from __future__ import annotations

import json

from openflight import unit_check as uc


def test_the_camera_passes_when_libcamera_lists_one():
    listing = (
        "Available cameras\n-----------------\n0 : ov9281 [1280x800 10-bit MONO] (/base/axi)\n"
    )
    result = uc.check_camera(lambda _cmd: (0, listing))
    assert result["ok"] is True
    assert result["detail"] == "found ov9281"
    assert result["remedy"] is None


def test_no_camera_names_the_camera_and_its_remedy():
    result = uc.check_camera(lambda _cmd: (0, "No cameras available!\n"))
    assert result["ok"] is False
    assert result["name"] == "Camera (OV9281)"
    assert "No cameras available!" in result["detail"]
    assert "ribbon cable" in result["remedy"]


def test_the_speed_radar_passes_with_a_firmware_reply():
    result = uc.check_ops("/dev/ttyAMA0", lambda _port: "1.2.3")
    assert result == {
        "id": "ops243",
        "name": "Speed radar (OPS243)",
        "ok": True,
        "detail": "firmware 1.2.3 on /dev/ttyAMA0",
        "remedy": None,
    }


def test_a_silent_or_missing_speed_radar_fails_plainly():
    silent = uc.check_ops("/dev/ttyAMA0", lambda _port: None)
    assert silent["ok"] is False and "no firmware reply" in silent["detail"]

    def missing(_port):
        raise ConnectionError("Failed to connect to /dev/ttyAMA0: no such device")

    gone = uc.check_ops("/dev/ttyAMA0", missing)
    assert gone["ok"] is False
    assert "no such device" in gone["detail"]


def test_the_ranging_radar_runs_the_cli_check_with_its_port_and_reset(capsys):
    seen = []

    def run(command):
        seen.append(list(command))
        return 0, 'IWR6843 CLI evidence: {"result": "ready"}\nIWR6843 CLI ready on /dev/ttyUSB0\n'

    result = uc.check_iwr("/dev/ttyUSB0", "pressed", run)
    assert result["ok"] is True
    assert result["detail"] == "IWR6843 CLI ready on /dev/ttyUSB0"
    assert seen[0][-4:] == ["--port", "/dev/ttyUSB0", "--operator-reset", "pressed"]
    assert seen[0][1].endswith("check_cli.py")
    # the evidence line still reaches the job log
    assert "IWR6843 CLI evidence" in capsys.readouterr().out


def test_a_failed_ranging_radar_keeps_the_cli_reason():
    result = uc.check_iwr(
        None, None, lambda _c: (1, "IWR6843 CLI check failed: no IWR6843 CLI found\n")
    )
    assert result["ok"] is False
    assert result["detail"] == "IWR6843 CLI check failed: no IWR6843 CLI found"
    assert "RESET" in result["remedy"]


def test_all_three_run_even_when_one_fails(tmp_path, monkeypatch):
    calls = []
    ok = lambda sensor: {"id": sensor, "name": uc.SENSOR_NAMES[sensor], "ok": True}  # noqa: E731

    result = uc.run_checks(
        ops_port="/dev/ttyAMA0",
        iwr_port=None,
        operator_reset=None,
        ops=lambda _p: calls.append("ops") or {**ok("ops243"), "ok": False},
        camera=lambda: calls.append("camera") or ok("camera"),
        iwr=lambda _p, _r: calls.append("iwr") or ok("iwr6843"),
    )
    assert calls == ["ops", "camera", "iwr"]
    assert result["all_ok"] is False
    assert result["sensors"]["ops243"]["ok"] is False


def test_main_writes_the_result_and_exits_by_it(tmp_path, monkeypatch):
    def fake(**kwargs):
        sensors = {
            sensor: {
                "id": sensor,
                "name": uc.SENSOR_NAMES[sensor],
                "ok": sensor != "camera",
                "detail": "x",
            }
            for sensor in uc.SENSORS
        }
        return {"schema": uc.SCHEMA, "sensors": sensors, "all_ok": False}

    monkeypatch.setattr(uc, "run_checks", fake)
    out = tmp_path / "t" / uc.RESULT_FILE

    assert uc.main(["--out", str(out)]) == 1
    assert uc.read_result(out)["sensors"]["camera"]["ok"] is False
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == uc.SCHEMA


def test_a_foreign_or_partial_result_is_not_read(tmp_path):
    path = tmp_path / uc.RESULT_FILE
    path.write_text('{"schema": "x"}', encoding="utf-8")
    assert uc.read_result(path) is None
    path.write_text(json.dumps({"schema": uc.SCHEMA, "sensors": {"ops243": {}}}), encoding="utf-8")
    assert uc.read_result(path) is None
    assert uc.read_result(tmp_path / "missing.json") is None
