"""The kiosk takes the camera's estimate as a prior and ranges each shot.

The setup record's ball range is a prior plus its uncertainty; each shot's own IWR launch
point (or the labelled prior) is the range its camera scale, ball speed and club delivery
use."""

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


def test_the_setup_record_s_ball_range_is_the_iwr_s_prior(monkeypatch):
    runtime = SimpleNamespace(range_prior=None, calibration=None)
    monkeypatch.setattr(server_module, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server_module, "iwr6843_runtime_config", {"enabled": True})
    monkeypatch.setattr(server_module, "ball_at_address_monitor", None)
    monkeypatch.setattr(server_module, "_ball_at_address_range", (None, None))
    monkeypatch.setattr(server_module, "camera_capture_config", {"enabled": False})
    monkeypatch.setattr(server_module, "setup_handoff_config", {})
    monkeypatch.setattr(server_module, "shot_processing_pending", lambda: False)

    server_module.apply_setup_ball_range(
        {"state": "set", "range_m": 2.11, "uncertainty_m": 0.42, "source": "camera_size_coarse"}
    )

    assert runtime.range_prior == PRIOR
    assert server_module.iwr6843_runtime_config["tee_range_status"] == (
        "per_shot_with_camera_prior"
    )
    assert server_module.setup_handoff_config["tee_range"]["tee_slant_range_m"] is None


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


def test_same_clip_conflict_rejects_prior_before_iwr_fitting(monkeypatch):
    from openflight.camera.fallback_chain import RESTING_BALL_KEY, RestingBallMeasure
    from openflight.camera.resting_ball_range import RestingBallSize

    seen = {}
    seed = {"x": 380.0, "y": 195.0, "diameter_px": 13.5}
    archive = {
        RESTING_BALL_KEY: RestingBallMeasure(
            seed=(380.0, 195.0, 13.5),
            found=(380.0, 195.0, 13.5),
            size=RestingBallSize(409.0, 199.0, 21.4, 1.0, 6),
        )
    }

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(capture=None, measurement=None, club_path=None)

    def snapshot(**kwargs):
        seen["snapshot"] = kwargs
        return {"sha256": "test"}

    monkeypatch.setattr(
        server_module,
        "iwr6843_runtime",
        SimpleNamespace(
            process_shot=process_shot, replay_config_snapshot=snapshot, range_prior=PRIOR
        ),
    )
    monkeypatch.setattr(server_module, "camera_capture_runtime", object())
    monkeypatch.setattr(
        server_module,
        "_prefetch_camera_capture",
        lambda _shot: server_module._CameraPrefetchResult(None, archive, 0.0, 0.0, 0.0),
    )
    monkeypatch.setattr(
        server_module,
        "shot_ball_at_address",
        lambda _ts: {
            "state": "in_box",
            "range_m": 3.2,
            "range_uncertainty_m": 0.5,
            "center_px": [seed["x"], seed["y"]],
            "diameter_px": seed["diameter_px"],
        },
    )
    monkeypatch.setattr(server_module, "_snapshot_inclinometer_for_shot", lambda _shot: None)
    monkeypatch.setattr(server_module, "_attach_camera_replay", lambda *_args: None)
    monkeypatch.setattr(server_module, "_fuse_camera_measurements", lambda *_args: None)
    monkeypatch.setattr(server_module, "kld7_vertical", None)
    monkeypatch.setattr(server_module, "kld7_horizontal", None)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    shot = _shot()

    server_module._enrich_shot_from_optional_hardware(shot)

    assert seen.get("range_prior_rejection"), "conflicting camera prior reached IWR fitting"
    assert seen["snapshot"]["range_prior_rejection"] == seen["range_prior_rejection"]
    assert shot.input_sources["ball_reference"]["status"] == "conflict"


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


@pytest.mark.parametrize("ranged", [True, False])
def test_the_camera_and_full_speed_use_the_shot_s_own_range(monkeypatch, ranged):
    calibration = Calibration.identity()
    calibration.tee_range_m = None
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace(calibration=calibration))
    shot = _shot()
    if ranged:
        shot.ball_range_m = 2.08
        shot.ball_range_uncertainty_m = 0.03
        shot.ball_range_source = "iwr_post_impact"

    shot_calibration = server_module.shot_iwr_calibration(shot)

    assert shot_calibration.tee_range_m == (2.08 if ranged else None)
    if ranged:
        assert shot_calibration.tee_range_uncertainty_m == 0.03
    assert calibration.tee_range_m is None  # the session's calibration is never changed


SETUP_PRIOR = lr.LaunchRangePrior(range_m=1.80, uncertainty_m=0.38, source="camera_prior")
# 27 Oct shot 2's look: a 13.5 px object taken for the ball
FAR_LOOK = {
    "state": "in_box",
    "range_m": 3.217,
    "range_uncertainty_m": 0.68,
    "range_source": "camera_size_ratio_to_the_setup_camera_prior",
    "center_px": [381.2, 194.9],
    "diameter_px": 13.52,
}


def test_the_iwr_stage_records_why_the_shot_s_look_was_refused(monkeypatch):
    def process_shot(**_kwargs):
        return SimpleNamespace(capture=None, measurement=None, club_path=None, withheld_reason=None)

    monkeypatch.setattr(
        server_module,
        "iwr6843_runtime",
        SimpleNamespace(process_shot=process_shot, range_prior=SETUP_PRIOR),
    )
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module, "shot_ball_at_address", lambda _ts: FAR_LOOK)
    shot = _shot()

    server_module._process_iwr6843_angle(shot)  # pylint: disable=protected-access

    choice = shot.input_sources["range_prior_choice"]
    assert choice["chosen"] == "setup"
    assert choice["rejection"] == lr.PER_SHOT_PRIOR_DISAGREES
    assert "3.22" in choice["reason"] and "1.80" in choice["reason"]


def test_without_an_iwr_range_the_chain_takes_the_setup_prior_over_a_far_look(monkeypatch):
    monkeypatch.setattr(
        server_module,
        "iwr6843_runtime",
        SimpleNamespace(range_prior=SETUP_PRIOR, calibration=None),
    )
    monkeypatch.setattr(server_module, "shot_ball_at_address", lambda _ts: FAR_LOOK)
    shot = _shot()

    server_module._resolve_shot_ball_range(shot, None)  # pylint: disable=protected-access

    assert "ball_range_error" not in shot.input_sources
    assert shot.ball_range_m == pytest.approx(1.80)
    assert shot.ball_range_source == "camera_prior"
    assert shot.input_sources["range_prior_choice"]["rejection"] == lr.PER_SHOT_PRIOR_DISAGREES


def test_a_look_that_agrees_with_the_setup_prior_is_the_chain_s_prior(monkeypatch):
    monkeypatch.setattr(
        server_module,
        "iwr6843_runtime",
        SimpleNamespace(range_prior=SETUP_PRIOR, calibration=None),
    )
    monkeypatch.setattr(
        server_module,
        "shot_ball_at_address",
        lambda _ts: {**FAR_LOOK, "range_m": 1.60, "range_uncertainty_m": 0.34},
    )
    shot = _shot()

    server_module._resolve_shot_ball_range(shot, None)  # pylint: disable=protected-access

    assert shot.ball_range_m == pytest.approx(1.60)
    assert shot.input_sources["range_prior_choice"]["chosen"] == "per_shot"
