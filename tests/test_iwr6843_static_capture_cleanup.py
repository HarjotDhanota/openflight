"""A failed static capture and a failed cleanup still leave the IWR port free."""

from openflight.iwr6843.static_capture import StaticCaptureInputs, capture_static_range
from tests import test_iwr6843_serial_lifecycle as lifecycle
from tests.test_iwr6843_serial_lifecycle import BY_ID, CHECK, _lock_is_free

# the lifecycle tests' simulated board and port, shared
board = lifecycle.board


def test_failed_static_capture_and_failed_cleanup_leave_the_port_free(board, tmp_path):
    for name in ("radar.cfg", "firmware.bin", "rig.json", "calibration.json"):
        (tmp_path / name).write_bytes(b"sensorStart\n")
    inputs = StaticCaptureInputs(
        capture_id="empty-001",
        capture_kind="empty",
        output_dir=tmp_path / "iwr",
        config_path=tmp_path / "radar.cfg",
        firmware_path=tmp_path / "firmware.bin",
        rig_geometry_path=tmp_path / "rig.json",
        calibration_path=tmp_path / "calibration.json",
        port=BY_ID,
        settle_s=0.25,
    )
    answer = board.firmware.receive
    board.firmware.receive = lambda _data: None

    result = capture_static_range(inputs, wait_for_settle=lambda *_args: False)

    assert result["error"]["stage"] == "configure"
    assert [error["operation"] for error in result["cleanup_errors"]] == ["stop_sensor"]
    assert board.open_handles() == []
    assert _lock_is_free(board)

    board.firmware.receive = answer
    assert CHECK.main(["--port", BY_ID]) == 0
    assert _lock_is_free(board)
