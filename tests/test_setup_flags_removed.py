"""The setup record is the only way the box, aim, ball range, net and scene reach the kiosk:
the old setup flags are refused, and the device and unit flags still parse."""

from __future__ import annotations

import pytest

from openflight import server

REMOVED = [
    ("--camera-ball-box", "480,380,800,520"),
    ("--camera-hitting-zone", "480,380,800,520"),
    ("--camera-setup-ball", "640,450,20"),
    ("--target-line-yaw-deg", "2.4"),
    ("--target-line-uncertainty-deg", "0.9"),
    ("--target-line-source", "aim_point"),
    ("--target-line-status", "set"),
    ("--camera-capture-horizontal-offset-deg", "-2.4"),
    ("--iwr6843-azimuth-offset-deg", "-2.4"),
    ("--iwr6843-tee-m", "2.2"),
    ("--iwr6843-tee-prior-m", "2.2"),
    ("--iwr6843-tee-prior-uncertainty-m", "0.1"),
    ("--iwr6843-tee-prior-source", "camera_size_coarse"),
    ("--iwr6843-tee-range-pending", None),
    ("--iwr6843-tee-range-source", "qualified_static_iwr"),
    ("--iwr6843-tee-range-candidate", "c1"),
    ("--tee-side-offset-m", "0.05"),
    ("--iwr6843-net-m", "4.0"),
    ("--net-range-m", "4.0"),
    ("--iwr6843-net-range-source", "empty_static_capture"),
    ("--solved-camera-height-m", "0.4"),
    ("--scene-lens-height-solved-m", "0.4"),
    ("--scene-lens-height-solved-uncertainty-m", "0.02"),
    ("--camera-capture-mount-height-m", "0.2"),
    ("--camera-capture-lateral-offset-m", "0.05"),
    ("--camera-capture-forward-offset-m", "0.05"),
    ("--iwr6843-tilt-deg", "5"),
    ("--iwr6843-radar-height-m", "0.15"),
    ("--inclinometer-zero-offset", "0.5"),
    ("--camera-capture-roll-deg", "1.0"),
]

KEPT = [
    ["--port", "/dev/ttyACM0"],
    ["--iwr6843", "--iwr6843-port", "/dev/ttyUSB0", "--iwr6843-config", "x.cfg"],
    ["--camera-capture", "--camera-capture-width", "1280", "--camera-capture-height", "800"],
    ["--inclinometer", "--inclinometer-bus", "1"],
    ["--rig-geometry", "config/enclosure_v3_rig_geometry.json"],
    ["--camera-lens-id", "6mm"],
    ["--camera-tilt-calibration-path", "tilt.json"],
    ["--camera-vertical-offset-deg", "1.2", "--camera-vertical-offset-source", "unit_calibration"],
    ["--handed", "LH"],
    ["--lis3dh-mount-file", "mount.json"],
    ["--camera-light-index", "0.5"],
    ["--setup-file", "setup.json"],
    ["--iwr6843-ball-height-m", "0.03"],
    ["--net-distance", "12"],
]


@pytest.mark.parametrize(("flag", "value"), REMOVED, ids=[flag for flag, _ in REMOVED])
def test_removed_setup_flags_are_rejected(flag, value, capsys):
    argv = [flag] if value is None else [flag, value]

    with pytest.raises(SystemExit):
        server.build_parser().parse_args(argv)

    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize("argv", KEPT, ids=[argv[0] for argv in KEPT])
def test_kept_flags_still_parse(argv):
    server.build_parser().parse_args(argv)


def test_an_extension_s_flags_still_parse():
    # conftest loads the installed extensions, the tester's among them
    args = server.build_parser().parse_args(
        ["--tester-setup-required", "--tester-config-hash", "abc"]
    )

    assert args.tester_setup_required
