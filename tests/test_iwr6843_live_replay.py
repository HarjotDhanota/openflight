"""Replay must execute the live launch-range path before the angle estimator."""

from types import SimpleNamespace

import pytest

from openflight import raw_radar_replay
from openflight.iwr6843 import Calibration, runtime
from openflight.iwr6843.club import ClubWindowPolicy
from openflight.iwr6843.launch_range import LaunchRangeEstimate, LaunchRangePrior


@pytest.mark.parametrize(
    "measured,prior,rejected",
    [(True, False, False), (False, True, False), (False, True, True), (False, False, False)],
)
def test_replay_resolves_or_withholds_range_like_live(monkeypatch, measured, prior, rejected):
    cal = Calibration.identity()
    cal.meta["radar_height_m"] = 0.05
    cal.lateral_tee_offset_m = -0.064
    supplied_prior = LaunchRangePrior(1.9, 0.3, "camera_test") if prior else None
    monkeypatch.setattr(runtime, "prepare_lcmf_capture", lambda raw: SimpleNamespace(raw=raw))
    seen = {}

    def launch(self, prepared, calibration, **kwargs):
        assert prepared.raw == b"own-shot"
        assert calibration.lateral_tee_offset_m == -0.064
        assert kwargs["prior"] == (None if rejected else supplied_prior)
        return LaunchRangeEstimate(
            status="measured" if measured else "not_measured",
            reason="test range",
            range_m=1.7 if measured else None,
            uncertainty_m=0.08 if measured else None,
        )

    def estimate(raw, calibration, **kwargs):
        seen["range"] = calibration.tee_range_m
        seen["uncertainty"] = calibration.tee_range_uncertainty_m
        return SimpleNamespace(status="accepted_test"), None

    monkeypatch.setattr(runtime.IWR6843Runtime, "_launch_range", launch)
    monkeypatch.setattr(runtime, "process_raw_capture", estimate)
    result = raw_radar_replay.replay_iwr_shot_bytes(
        b"own-shot",
        cal,
        ball_speed_mph=90,
        club="7-iron",
        club_speed_mph=None,
        net_range_m=4.7,
        tx_order="normal",
        tdm_sign_policy="positive",
        azimuth_offset_deg=0,
        club_window_policy=ClubWindowPolicy(),
        club_impact_correction_s=0,
        range_prior=supplied_prior,
        range_prior_rejection="rejected own prior" if rejected else None,
    )

    if measured or (prior and not rejected):
        assert result.measurement.status == "accepted_test"
        assert seen["range"] == (1.7 if measured else 1.9)
        assert seen["uncertainty"] == (0.08 if measured else 0.3)
        assert result.ball_range.source == ("iwr_post_impact" if measured else "camera_prior")
    else:
        assert result.measurement is None
        assert result.withheld_reason == "tee_range_unresolved"
        assert not seen
    assert cal.tee_range_m is None
