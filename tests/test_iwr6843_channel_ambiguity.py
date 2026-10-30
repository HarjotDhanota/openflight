"""Disagreeing model fits need a clear conditioning advantage to select one."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openflight.iwr6843 import lcmf, runtime


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize(
    "scores",
    [
        (0.0060324455764464435, 0.005548494927799519),
        (0.005104030258406667, 0.005554370385463692),
        (1.0, 1.0),
    ],
)
def test_conflicting_near_tied_models_are_withheld_independent_of_order_and_scale(
    reverse, scale, scores
):
    names = ["channel_two8_deg", "channel_four4_path_tdm_deg"]
    components = dict(zip(names, (8.9, 21.0)))
    evidence = {name: score * scale for name, score in zip(names, scores)}
    if reverse:
        components = dict(reversed(list(components.items())))

    angle, used, single = lcmf.combine_channels(components, evidence)

    assert angle is None
    assert used == []
    assert single is False


@pytest.mark.parametrize("ratio, selected", [(1.99, False), (2.0, True), (2.01, True)])
def test_curvature_dominance_policy_boundary(ratio, selected):
    angle, used, single = lcmf.combine_channels({"a": 9.0, "b": 21.0}, {"a": ratio, "b": 1.0})

    assert (angle is not None) is selected
    if selected:
        assert angle == 9.0 and used == ["a"] and single


def test_agreeing_models_do_not_need_curvature_dominance():
    assert lcmf.combine_channels({"a": 9.0, "b": 10.0}, {"a": 1.0, "b": 1.0}) == (
        9.5,
        ["a", "b"],
        False,
    )


@pytest.mark.parametrize(
    "speed,quality,ambiguous,search",
    [
        (105.0, "ok", None, False),
        (130.0, "ok", None, True),
        (105.0, "reject", None, True),
        (105.0, "ok", {"reason": "competing tracks"}, True),
    ],
)
def test_channel_disagreement_cannot_be_evaded_by_refitting_a_sound_track(
    speed, quality, ambiguous, search
):
    baseline = lcmf.LCMFResult(
        status="rejected_channel_ambiguous",
        track_speed_mph=speed,
        tracker_quality=quality,
        track_ambiguity=ambiguous,
    )

    with patch("openflight.iwr6843.runtime.find_recovery_candidates", return_value=[]) as find:
        result = runtime.ops_guided_measurement(
            b"unused",
            object(),
            ball_speed_mph=100.0,
            club="7i",
            baseline=baseline,
            prepared=SimpleNamespace(vertical=object()),
            net_range_m=4.6,
            tx_order="normal",
            tdm_sign_policy="positive",
            horizontal_phase_reference_rad=None,
        )

    assert find.called is search
    assert result is baseline
