"""The kiosk takes the camera's estimate as a prior and ranges each shot.

``--iwr6843-tee-prior-m`` is a prior plus its uncertainty; each shot's own IWR launch point
(or the labelled prior) is the range its camera scale, ball speed and club delivery use."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.clubs import ClubType
from openflight.iwr6843 import launch_range as lr
from openflight.iwr6843.calibration import Calibration
from openflight.launch_monitor import Shot

PRIOR = lr.LaunchRangePrior(range_m=2.11, uncertainty_m=0.42, source="camera_size_coarse")


def _parse(*extra):
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser()
    server_module._add_iwr_tee_range_arguments(parser)  # pylint: disable=protected-access
    return parser.parse_args(list(extra))


def test_the_prior_flags_parse():
    args = _parse(
        "--iwr6843-tee-prior-m",
        "2.11",
        "--iwr6843-tee-prior-uncertainty-m",
        "0.42",
        "--iwr6843-tee-prior-source",
        "camera_size_coarse",
    )

    assert server_module.range_prior_from_args(args) == PRIOR


def test_without_the_flags_there_is_no_prior_and_nothing_is_filled_in():
    assert server_module.range_prior_from_args(_parse()) is None


def test_a_prior_without_its_uncertainty_is_refused():
    args = _parse("--iwr6843-tee-prior-m", "2.11")

    with pytest.raises(ValueError, match="uncertainty"):
        server_module.range_prior_from_args(args)


def _shot():
    return Shot(
        ball_speed_mph=100.0,
        club_speed_mph=80.0,
        timestamp=datetime.now(),
        impact_timestamp=100.0,
        club=ClubType.IRON_7,
    )


def _capture():
    return SimpleNamespace(
        trigger_timestamp=100.01,
        path=Path("/tmp/test.l3dump"),
        raw=b"raw",
        dump_duration_s=4.5,
        error=None,
        valid=True,
        sequence=1,
    )


def test_the_shot_records_the_range_its_runtime_used(monkeypatch):
    seen = {}
    launch = lr.LaunchRangeEstimate(
        status="measured",
        reason=None,
        method="club_ball_intersection",
        range_m=2.08,
        uncertainty_m=0.03,
    )
    ball_range = lr.shot_ball_range(launch, PRIOR)

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            capture=_capture(),
            measurement=None,
            club_path=None,
            withheld_reason=None,
            launch_range=launch,
            ball_range=ball_range,
        )

    monkeypatch.setattr(
        server_module, "iwr6843_runtime", SimpleNamespace(process_shot=process_shot)
    )
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(
        server_module,
        "shot_ball_at_address",
        lambda _ts: {
            "state": "in_box",
            "range_m": 1.98,
            "range_uncertainty_m": 0.39,
            "range_source": "setup_camera_prior",
        },
    )
    shot = _shot()

    server_module._process_iwr6843_angle(shot)  # pylint: disable=protected-access

    # the shot's own resting-ball look is its prior
    assert seen["range_prior"] == lr.LaunchRangePrior(1.98, 0.39, "camera_ball_at_address")
    assert "tee_range_m" not in seen
    assert shot.ball_range_m == 2.08
    assert shot.ball_range_source == "iwr_post_impact"
    assert shot.ball_range_uncertainty_m == 0.03
    assert shot.ball_range_label == "measured"
    assert shot.iwr_launch_range["method"] == "club_ball_intersection"


def test_a_shot_without_an_iwr_capture_says_why_its_range_is_missing(monkeypatch):
    monkeypatch.setattr(
        server_module,
        "iwr6843_runtime",
        SimpleNamespace(
            process_shot=lambda **_kw: SimpleNamespace(
                capture=None, measurement=None, club_path=None, withheld_reason=None
            )
        ),
    )
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module, "shot_ball_at_address", lambda _ts: None)
    shot = _shot()

    server_module._process_iwr6843_angle(shot)  # pylint: disable=protected-access

    assert shot.ball_range_m is None
    assert shot.ball_range_label == "not_measured"
    assert "no IWR capture" in shot.ball_range_reason


def test_the_camera_and_full_speed_use_the_shot_s_own_range(monkeypatch):
    calibration = Calibration.identity()
    calibration.tee_range_m = None
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace(calibration=calibration))
    shot = _shot()
    shot.ball_range_m = 2.08
    shot.ball_range_uncertainty_m = 0.03
    shot.ball_range_source = "iwr_post_impact"

    shot_calibration = server_module.shot_iwr_calibration(shot)

    assert shot_calibration.tee_range_m == 2.08
    assert shot_calibration.tee_range_uncertainty_m == 0.03
    assert calibration.tee_range_m is None  # the session's calibration is never changed


def test_a_shot_without_a_range_has_no_range_for_the_camera(monkeypatch):
    calibration = Calibration.identity()
    calibration.tee_range_m = None
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace(calibration=calibration))

    assert server_module.shot_iwr_calibration(_shot()).tee_range_m is None
