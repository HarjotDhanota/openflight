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
        "iwr_partial",
        "experimental",
        "not_measured",
    )


def test_a_partial_iwr_label_ranks_between_estimated_and_experimental():
    assert "iwr_partial" in source_chain.LABEL_ORDER
    assert weakest_label("measured", "iwr_partial") == "iwr_partial"
    assert weakest_label("estimated", "iwr_partial") == "iwr_partial"
    assert weakest_label("iwr_partial", "experimental") == "experimental"
    Candidate(2.1, "iwr_partial", "iwr_post_impact")


# ---------------------------------------------------------------- strongest label first (30 Oct 2026)


def test_the_stronger_label_wins_and_the_loser_says_why_it_lost():
    resolution = source_chain.resolve_strongest(
        "ball_speed_full",
        [
            Route("measured_projection", lambda: Candidate(96.3, "experimental", "projection")),
            Route("ops_total_projection", lambda: Candidate(79.4, "estimated", "total")),
        ],
    )

    assert (resolution.value, resolution.label, resolution.route) == (
        79.4,
        "estimated",
        "ops_total_projection",
    )
    ((name, why),) = resolution.tried
    assert name == "measured_projection"
    assert "96.30" in why and "experimental" in why
    assert "ops_total_projection" in why and "estimated" in why


def test_a_tie_keeps_the_route_order_and_records_the_runner_up():
    resolution = source_chain.resolve_strongest(
        "ball_speed_full",
        [
            Route("measured_projection", lambda: Candidate(88.0, "calculated", "projection")),
            Route("ops_total_projection", lambda: Candidate(87.5, "calculated", "total")),
        ],
    )

    assert resolution.route == "measured_projection"
    ((name, why),) = resolution.tried
    assert name == "ops_total_projection"
    assert "same label" in why


def test_with_one_value_the_strongest_resolution_is_the_first_route_s():
    routes = [
        Route("measured_projection", lambda: "no horizontal launch"),
        Route("ops_total_projection", lambda: Candidate(87.5, "experimental", "total")),
    ]

    strongest = source_chain.resolve_strongest("ball_speed_full", routes)

    assert strongest == resolve("ball_speed_full", routes)


def test_every_route_runs_and_an_error_is_a_refusal_in_order():
    def broken():
        raise ValueError("broken projection")

    resolution = source_chain.resolve_strongest(
        "ball_speed_full",
        [
            Route("measured_projection", broken),
            Route("ops_total_projection", lambda: Candidate(87.5, "calculated", "total")),
            Route("third", lambda: Candidate(90.0, "experimental", "third")),
        ],
    )

    assert resolution.route == "ops_total_projection"
    assert [name for name, _why in resolution.tried] == ["measured_projection", "third"]
    assert "broken projection" in resolution.tried[0][1]
    assert "outranks" in resolution.tried[1][1]
