"""Full speed uses the aim that produced this shot's reported horizontal angle."""

from copy import deepcopy

import pytest

import openflight.server as server
from openflight import shot_models
from openflight.horizontal_frame import TARGET_LINE, UNIT_BORESIGHT, to_reported_deg
from tests.test_full_speed_chain import fusion_geometry  # noqa: F401
from tests.test_full_speed_server import _shot

pytestmark = pytest.mark.usefixtures("fusion_geometry")


def attach(shot):
    shot_models._attach_full_speeds(shot)  # pylint: disable=protected-access
    return shot.ball_speed_full


@pytest.mark.parametrize("saved_offset", [-5.0, 0.0, 7.0])
@pytest.mark.parametrize("current_offset", [None, 12.0])
def test_saved_aim_recovers_the_same_physical_speed(monkeypatch, saved_offset, current_offset):
    expected = attach(_shot())
    line = {"frame": TARGET_LINE, "horizontal_offset_deg": saved_offset, "revision": 4}
    shot = _shot(
        launch_angle_horizontal=to_reported_deg(2.0, saved_offset),
        launch_angle_horizontal_frame=TARGET_LINE,
        target_line=deepcopy(line),
    )
    monkeypatch.setattr(server, "horizontal_offset_deg", current_offset)

    full = attach(shot)

    assert full["route"] == "measured_projection"
    assert full["value_mph"] == pytest.approx(expected["value_mph"], abs=1e-10)
    assert full["inputs"]["horizontal_boresight_deg"] == pytest.approx(2.0)
    assert full["inputs"]["horizontal_offset_deg"] == saved_offset
    assert full["status"] == expected["status"]
    assert shot.target_line == line
    assert shot.launch_angle_horizontal == to_reported_deg(2.0, saved_offset)


def test_reprocessing_retains_speed_record_when_current_aim_changes(monkeypatch):
    shot = _shot(
        launch_angle_horizontal=5.0,
        launch_angle_horizontal_frame=TARGET_LINE,
        target_line={"frame": TARGET_LINE, "horizontal_offset_deg": 3.0, "revision": 9},
    )
    monkeypatch.setattr(server, "horizontal_offset_deg", 3.0)
    first = deepcopy(attach(shot))
    monkeypatch.setattr(server, "horizontal_offset_deg", -14.0)

    assert attach(shot) == first


@pytest.mark.parametrize(
    "line",
    [
        None,
        {},
        [],
        {"frame": UNIT_BORESIGHT, "horizontal_offset_deg": 3.0},
        {"frame": TARGET_LINE},
        {"frame": TARGET_LINE, "horizontal_offset_deg": float("nan")},
        {"frame": TARGET_LINE, "horizontal_offset_deg": float("inf")},
    ],
)
def test_unbound_target_angle_cannot_borrow_current_aim(monkeypatch, line):
    monkeypatch.setattr(server, "horizontal_offset_deg", 3.0)
    shot = _shot(
        launch_angle_horizontal=5.0,
        launch_angle_horizontal_frame=TARGET_LINE,
        target_line=line,
    )

    full = attach(shot)

    assert full["route"] == "ops_total_projection"
    tried = shot.input_sources["ball_speed_full"]["tried"]
    assert tried[0]["route"] == "measured_projection"
    assert "horizontal offset" in tried[0]["reason"]


@pytest.mark.parametrize("current_offset", [None, -12.0, 0.0, 12.0])
def test_boresight_angle_needs_no_saved_aim(monkeypatch, current_offset):
    expected = attach(_shot())
    monkeypatch.setattr(server, "horizontal_offset_deg", current_offset)
    full = attach(_shot())

    assert full["route"] == "measured_projection"
    assert full["value_mph"] == expected["value_mph"]
    assert full["status"] == expected["status"]
