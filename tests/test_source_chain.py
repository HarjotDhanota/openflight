"""The best-available-source chain: each input takes the first route that survives."""

import pytest

from openflight import source_chain
from openflight.source_chain import Candidate, Route, resolve, weakest_label


def test_the_first_surviving_route_wins_and_every_earlier_refusal_is_kept():
    resolution = resolve(
        "ball_range",
        [
            Route("iwr_post_impact", lambda: "no IWR capture matched this shot"),
            Route("camera_prior", lambda: None),
            Route(
                "camera_ball_size",
                lambda: Candidate(1.95, "experimental", "camera_ball_size", uncertainty=0.17),
            ),
            Route("never_reached", lambda: pytest.fail("a later route must not run")),
        ],
    )

    assert resolution.present
    assert resolution.value == 1.95
    assert resolution.label == "experimental"
    assert resolution.source == "camera_ball_size"
    assert resolution.route == "camera_ball_size"
    assert resolution.uncertainty == 0.17
    assert resolution.tried == (
        ("iwr_post_impact", "no IWR capture matched this shot"),
        ("camera_prior", "camera_prior gave nothing"),
    )


def test_a_route_that_raises_is_refused_with_its_error_and_the_chain_goes_on():
    def broken():
        raise ValueError("bad frames")

    resolution = resolve(
        "launch_vertical",
        [
            Route("radar", broken),
            Route("camera_fallback", lambda: Candidate(14.0, "experimental", "camera_fallback")),
        ],
    )

    assert resolution.value == 14.0
    assert resolution.tried == (("radar", "error: ValueError: bad frames"),)


def test_no_surviving_route_is_not_measured_with_every_reason():
    resolution = resolve(
        "launch_vertical",
        [
            Route("radar", lambda: "LCMF withheld"),
            Route("camera_fallback", lambda: "one ball point"),
        ],
    )

    assert not resolution.present
    assert resolution.value is None
    assert resolution.label == "not_measured"
    assert resolution.reason == "radar: LCMF withheld; camera_fallback: one ball point"


def test_a_non_finite_candidate_is_refused_not_used():
    resolution = resolve(
        "launch_vertical",
        [
            Route("radar", lambda: Candidate(float("nan"), "measured", "radar")),
            Route("camera_fallback", lambda: Candidate(12.0, "experimental", "camera_fallback")),
        ],
    )

    assert resolution.value == 12.0
    assert resolution.tried == (("radar", "radar gave a non-finite value"),)


def test_an_unknown_label_is_a_code_error():
    with pytest.raises(ValueError, match="label"):
        Candidate(1.0, "great", "radar")


def test_the_record_is_plain_json():
    resolution = resolve(
        "ball_range",
        [
            Route("iwr", lambda: "none"),
            Route("cam", lambda: Candidate(2.0, "estimated", "camera_prior", note="coarse")),
        ],
    )

    assert resolution.to_dict() == {
        "value": 2.0,
        "label": "estimated",
        "source": "camera_prior",
        "route": "cam",
        "uncertainty": None,
        "note": "coarse",
        "reason": None,
        "tried": [{"route": "iwr", "reason": "none"}],
    }


@pytest.mark.parametrize(
    ("labels", "weakest"),
    [
        (("measured", "calculated"), "calculated"),
        (("calculated", "model_geometry"), "model_geometry"),
        (("model_geometry", "estimated"), "estimated"),
        (("estimated", "experimental", "measured"), "experimental"),
        (("measured", None), "measured"),
        ((None,), None),
        ((), None),
    ],
)
def test_the_weakest_label_wins(labels, weakest):
    assert weakest_label(*labels) == weakest


def test_not_measured_is_weaker_than_every_label():
    assert weakest_label("measured", "not_measured") == "not_measured"


def test_the_order_is_strongest_first():
    assert source_chain.LABEL_ORDER == (
        "measured",
        "calculated",
        "model_geometry",
        "estimated",
        "experimental",
        "not_measured",
    )
