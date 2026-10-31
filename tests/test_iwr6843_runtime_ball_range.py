"""The IWR runtime ranges each shot before anything uses the range.

Each shot's own capture gives its launch point; that range, or the camera prior
labelled, is what LCMF, the impact anchor and the club gate use for that shot.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openflight.iwr6843 import launch_range as lr
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.runtime import IWR6843Runtime


class FakeCapture:
    valid = True
    raw = b"x" * 32
    path = None
    error = None
    trigger_timestamp = 1.0
    dump_duration_s = 5.3
    sequence = 1


class FakeMonitor:
    def capture_for_shot(self, _ts, timeout_s):
        del timeout_s
        return FakeCapture()

    def stop(self):
        pass


PRIOR = lr.LaunchRangePrior(range_m=2.11, uncertainty_m=0.42, source="camera_size_coarse")
MISSING = lr.LaunchRangeEstimate(status="not_measured", reason="no ball track after impact")
SHOT = {"impact_timestamp": 1.0, "ball_speed_mph": 100.0, "club": "7i", "club_speed_mph": 74.0}


def _runtime(*, tee_range_m=None, prior=PRIOR):
    calibration = Calibration.identity()
    calibration.tee_range_m = tee_range_m
    return IWR6843Runtime(
        capture_monitor=FakeMonitor(),
        calibration=calibration,
        net_range_m=4.6,
        range_prior=prior,
    )


def _measured(range_m=2.08, uncertainty_m=0.03):
    return lr.LaunchRangeEstimate(
        status="measured",
        reason=None,
        method="club_ball_intersection",
        range_m=range_m,
        uncertainty_m=uncertainty_m,
    )


@pytest.fixture
def seen():
    calls = {}

    def fake_process(raw, calibration, **kwargs):
        calls["calibration"] = calibration
        calls["kwargs"] = kwargs
        return SimpleNamespace(accepted=True), None

    with (
        patch(
            "openflight.iwr6843.runtime.prepare_lcmf_capture",
            return_value=SimpleNamespace(vertical="vertical"),
        ),
        patch("openflight.iwr6843.runtime.process_raw_capture", side_effect=fake_process),
    ):
        yield calls


def test_a_measured_launch_point_is_the_range_every_estimator_uses(seen):
    with patch(
        "openflight.iwr6843.runtime.estimate_launch_range", return_value=_measured()
    ) as estimate:
        result = _runtime().process_shot(**SHOT)

    assert estimate.call_args.kwargs["prior"] == PRIOR
    assert estimate.call_args.kwargs["ball_speed_mph"] == 100.0
    assert seen["calibration"].tee_range_m == 2.08
    assert seen["calibration"].tee_range_uncertainty_m == 0.03
    assert result.ball_range.source == "iwr_post_impact"
    assert result.ball_range.label == "measured"
    assert result.launch_range.method == "club_ball_intersection"


def test_without_a_launch_point_the_camera_prior_stands_in_labelled(seen):
    with patch("openflight.iwr6843.runtime.estimate_launch_range", return_value=MISSING):
        result = _runtime().process_shot(**SHOT)

    assert seen["calibration"].tee_range_m == 2.11
    assert seen["calibration"].tee_range_uncertainty_m == 0.42
    assert result.ball_range.source == "camera_prior"
    assert result.ball_range.label == "estimated"


def test_a_shot_s_own_resting_ball_look_is_its_prior(seen):
    own = lr.LaunchRangePrior(range_m=1.95, uncertainty_m=0.39, source="camera_ball_at_address")
    with patch(
        "openflight.iwr6843.runtime.estimate_launch_range", return_value=MISSING
    ) as estimate:
        result = _runtime().process_shot(**SHOT, range_prior=own)

    assert estimate.call_args.kwargs["prior"] == own
    assert result.ball_range.range_m == 1.95


def test_without_either_the_range_dependent_estimators_are_withheld():
    with (
        patch(
            "openflight.iwr6843.runtime.prepare_lcmf_capture",
            return_value=SimpleNamespace(vertical="vertical"),
        ),
        patch("openflight.iwr6843.runtime.estimate_launch_range", return_value=MISSING),
        patch("openflight.iwr6843.runtime.process_raw_capture") as process,
    ):
        result = _runtime(prior=None).process_shot(**SHOT)

    process.assert_not_called()
    assert result.capture is not None and result.capture.valid
    assert result.measurement is None
    assert result.club_path is None
    assert result.withheld_reason == "tee_range_unresolved"
    assert result.ball_range.range_m is None
    assert result.ball_range.label == "not_measured"
    assert "no ball track after impact" in result.ball_range.reason


def test_a_failing_estimator_never_loses_the_shot(seen):
    with patch(
        "openflight.iwr6843.runtime.estimate_launch_range", side_effect=ValueError("bad dump")
    ):
        result = _runtime().process_shot(**SHOT)

    assert result.launch_range.status == "not_measured"
    assert "bad dump" in result.launch_range.reason
    assert result.ball_range.source == "camera_prior"


def test_a_configured_range_is_kept_and_the_launch_point_is_still_recorded(seen):
    with patch("openflight.iwr6843.runtime.estimate_launch_range", return_value=_measured()):
        result = _runtime(tee_range_m=1.372, prior=None).process_shot(**SHOT)

    # a bench run given --iwr6843-tee-m keeps it; the launch point is recorded beside it
    assert seen["calibration"].tee_range_m == 1.372
    assert result.ball_range.source == "configured_tee"
    assert result.launch_range.range_m == 2.08


def test_the_replay_snapshot_records_the_prior():
    snapshot = _runtime().replay_config_snapshot(ball_speed_mph=100.0, range_prior=PRIOR)

    assert snapshot["range_prior"] == PRIOR.to_dict()
    assert snapshot["per_shot_inputs"]["range_prior"] == PRIOR.to_dict()


def test_a_rejected_reference_cannot_restore_the_session_prior(seen):
    with patch(
        "openflight.iwr6843.runtime.estimate_launch_range", return_value=MISSING
    ) as estimate:
        result = _runtime().process_shot(
            **SHOT, range_prior=PRIOR, range_prior_rejection="ball reference conflicts with clip"
        )

    assert estimate.call_args.kwargs["prior"] is None
    assert "calibration" not in seen
    assert result.measurement is None
    assert result.ball_range.range_m is None
    assert "ball reference conflicts with clip" in result.ball_range.reason


def test_a_rejected_prior_does_not_discard_the_iwr_launch_point(seen):
    with patch(
        "openflight.iwr6843.runtime.estimate_launch_range", return_value=_measured()
    ) as estimate:
        result = _runtime().process_shot(**SHOT, range_prior_rejection="reference conflict")

    assert estimate.call_args.kwargs["prior"] is None
    assert seen["calibration"].tee_range_m == 2.08
    assert result.ball_range.source == "iwr_post_impact"


def test_the_replay_snapshot_preserves_prior_rejection():
    snapshot = _runtime().replay_config_snapshot(
        range_prior=PRIOR, range_prior_rejection="reference conflict"
    )

    assert snapshot["range_prior"] == PRIOR.to_dict()
    assert snapshot["per_shot_inputs"]["range_prior"] is None
    assert snapshot["per_shot_inputs"]["range_prior_rejection"] == "reference conflict"


def test_a_snapshot_replay_cannot_resurrect_the_rejected_default_prior(seen):
    import json

    snapshot = json.loads(
        json.dumps(
            _runtime().replay_config_snapshot(
                **{key: value for key, value in SHOT.items() if key != "impact_timestamp"},
                range_prior_rejection="reference conflict",
            )
        )
    )
    inputs = snapshot["per_shot_inputs"]
    restored = _runtime(prior=lr.LaunchRangePrior(**snapshot["range_prior"]))
    with patch(
        "openflight.iwr6843.runtime.estimate_launch_range", return_value=MISSING
    ) as estimate:
        result = restored.process_shot(
            **SHOT,
            range_prior=inputs["range_prior"],
            range_prior_rejection=inputs["range_prior_rejection"],
        )

    assert estimate.call_args.kwargs["prior"] is None
    assert result.measurement is None
    assert "calibration" not in seen


def test_rejecting_a_prior_keeps_an_explicit_configured_range(seen):
    with patch("openflight.iwr6843.runtime.estimate_launch_range", return_value=MISSING):
        result = _runtime(tee_range_m=1.372).process_shot(
            **SHOT, range_prior_rejection="reference conflict"
        )

    assert seen["calibration"].tee_range_m == 1.372
    assert result.ball_range.source == "configured_tee"


FAR_LOOK = lr.LaunchRangePrior(range_m=3.40, uncertainty_m=0.68, source="camera_ball_at_address")


def test_a_per_shot_prior_far_from_the_setup_prior_gives_way_to_it(seen):
    # 3.40 m is 1.29 m from the setup's 2.11 +/- 0.42 m: beyond 2 x 0.42 m
    with patch(
        "openflight.iwr6843.runtime.estimate_launch_range", return_value=MISSING
    ) as estimate:
        result = _runtime().process_shot(**SHOT, range_prior=FAR_LOOK)

    assert estimate.call_args.kwargs["prior"] == PRIOR
    assert seen["calibration"].tee_range_m == 2.11
    assert result.ball_range.range_m == 2.11
    assert result.ball_range.source == "camera_prior"
    assert "3.40" in result.ball_range.reason and "2.11" in result.ball_range.reason


def test_a_refused_per_shot_prior_never_vetoes_the_iwr_launch_point(seen):
    with patch(
        "openflight.iwr6843.runtime.estimate_launch_range", return_value=_measured()
    ) as estimate:
        result = _runtime().process_shot(**SHOT, range_prior=FAR_LOOK)

    assert estimate.call_args.kwargs["prior"] == PRIOR
    assert result.ball_range.source == "iwr_post_impact"
    assert result.ball_range.range_m == 2.08


def test_the_replay_snapshot_records_the_prior_used_and_why_the_look_was_refused():
    snapshot = _runtime().replay_config_snapshot(ball_speed_mph=100.0, range_prior=FAR_LOOK)

    inputs = snapshot["per_shot_inputs"]
    assert inputs["range_prior"] == PRIOR.to_dict()
    assert inputs["range_prior_choice"]["rejection"] == lr.PER_SHOT_PRIOR_DISAGREES
    assert inputs["range_prior_choice"]["per_shot"] == FAR_LOOK.to_dict()
