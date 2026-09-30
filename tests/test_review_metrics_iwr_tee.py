"""The review labels an IWR launch by the tee range it stands on (P7-11).

The vertical launch rests on the tee's slant range: with a guessed tee it moved
4-28 deg per +-0.25 m, yet any ``accepted*`` estimator status read "accepted".
Once the setup saves an experimental, unqualified range (D11) most swings get
one, and single-channel launches were common. Both are experimental now, and
the metric carries the tee's range and where it came from.
"""

import pytest

from openflight.review_metrics import review_replay


def _report(status="accepted_ops_guided", *, single_channel=None, horizontal=None):
    return {
        "stages": {
            "iwr6843": {
                "status": status,
                "launch_angle_deg": 21.5,
                "single_channel": single_channel,
                "tracker_quality": "high",
                "horizontal_deg": horizontal,
                "horizontal_confidence": 0.9,
                "horizontal_status": "hlcmf_v1_ok" if horizontal is not None else None,
                "club_path": None,
            }
        }
    }


def _tee(source, range_m=1.35, status="configured"):
    return {
        "tee_slant_range_m": range_m,
        "status": status,
        "source": source,
        "candidate_id": None,
    }


def _metrics(report, tee_range):
    return {m["key"]: m for m in review_replay(report, {}, tee_range=tee_range)["metrics"]}


def test_a_launch_on_a_qualified_tee_with_both_channels_is_accepted_and_names_its_tee():
    metric = _metrics(_report(), _tee("qualified_static_iwr", 1.42))["iwr_launch_vertical_deg"]

    assert metric["status"] == "accepted"
    assert metric["details"]["tee_range_m"] == pytest.approx(1.42)
    assert metric["details"]["tee_range_source"] == "qualified_static_iwr"
    assert metric["details"]["tee_range_qualified"] is True


@pytest.mark.parametrize(
    "source",
    [
        "unqualified_static_iwr",
        "unqualified_static_iwr_camera_steered",
        "command_line",
        "pending",
    ],
)
def test_a_launch_on_an_unqualified_tee_is_experimental(source):
    metric = _metrics(_report(), _tee(source))["iwr_launch_vertical_deg"]

    assert metric["status"] == "experimental"
    assert metric["value"] == pytest.approx(21.5)
    assert "not qualified" in metric["reason"] and source in metric["reason"]
    assert metric["details"]["tee_range_m"] == pytest.approx(1.35)
    assert metric["details"]["tee_range_source"] == source
    assert metric["details"]["tee_range_qualified"] is False


def test_a_launch_with_no_record_of_its_tee_is_experimental():
    metric = _metrics(_report(), None)["iwr_launch_vertical_deg"]

    assert metric["status"] == "experimental"
    assert metric["details"]["tee_range_source"] == "unknown"
    assert metric["details"]["tee_range_m"] is None


@pytest.mark.parametrize(
    "status, single_channel",
    [("accepted_ops_guided_single_channel", None), ("accepted_ops_guided", True)],
)
def test_a_single_channel_launch_is_experimental_even_on_a_qualified_tee(status, single_channel):
    metric = _metrics(_report(status, single_channel=single_channel), _tee("qualified_static_iwr"))[
        "iwr_launch_vertical_deg"
    ]

    assert metric["status"] == "experimental"
    assert "one receive channel only" in metric["reason"]


def test_the_horizontal_launch_follows_the_same_rule():
    unqualified = _metrics(_report(horizontal=-2.5), _tee("unqualified_static_iwr"))
    qualified = _metrics(_report(horizontal=-2.5), _tee("qualified_static_iwr"))

    assert unqualified["iwr_launch_horizontal_deg"]["status"] == "experimental"
    assert unqualified["iwr_launch_horizontal_deg"]["details"]["tee_range_source"] == (
        "unqualified_static_iwr"
    )
    assert qualified["iwr_launch_horizontal_deg"]["status"] == "accepted"


def test_a_rejected_launch_stays_rejected():
    metric = _metrics(_report("rejected_no_track"), _tee("unqualified_static_iwr"))[
        "iwr_launch_vertical_deg"
    ]

    assert metric["status"] == "rejected"
