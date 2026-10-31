"""A Shot's source text names its value's label; the predicates read it back."""

import pytest

from openflight import source_labels as labels


@pytest.mark.parametrize(
    ("source", "labels_read"),
    [
        ("measured (camera)", {"measured"}),
        ("calculated (D-plane tilt)", {"calculated"}),
        ("model geometry (D-plane tilt; club path from the arc prior)", {"model_geometry"}),
        ("estimated (D-plane tilt; launch direction weak)", {"estimated"}),
        ("radar", set()),
        ("", set()),
        (None, set()),
    ],
)
def test_the_predicates_read_the_label_from_the_source_text(source, labels_read):
    assert labels.is_measured_source(source) is ("measured" in labels_read)
    assert labels.is_calculated_source(source) is ("calculated" in labels_read)
    assert labels.is_model_geometry_source(source) is ("model_geometry" in labels_read)
    assert labels.is_estimated_source(source) is ("estimated" in labels_read)


def test_the_label_words_are_the_shared_vocabulary():
    assert (
        labels.LABEL_MEASURED,
        labels.LABEL_CALCULATED,
        labels.LABEL_MODEL_GEOMETRY,
        labels.LABEL_ESTIMATED,
    ) == ("measured", "calculated", "model_geometry", "estimated")


def test_experimental_is_a_label_word_but_never_a_model_label():
    assert labels.LABEL_EXPERIMENTAL == "experimental"
    assert labels.LABEL_EXPERIMENTAL not in labels.MODEL_LABELS


# ---------------------------------------------------------------- the label policy (25 Oct 2026)


@pytest.mark.parametrize(
    ("tier", "intrinsics", "source", "label"),
    [
        ("high", "calibrated", "camera_fused_chained", "measured"),
        ("high", "nominal", "camera_fused_chained", "estimated"),
        ("high", None, "camera_fused_chained", "estimated"),
        ("experimental", "calibrated", "camera_fused_chained", "experimental"),
        ("medium", "calibrated", "camera_fused_chained", "experimental"),
        ("low", "calibrated", "camera_fused_chained", "experimental"),
        (None, "calibrated", "camera_fused_chained", "experimental"),
        # camera + OPS: no IWR depth under the club, whatever the tier
        ("high", "calibrated", "camera_fused_ops", "experimental"),
    ],
)
def test_one_function_labels_a_camera_club_value_by_its_tier_and_lens(
    tier, intrinsics, source, label
):
    assert labels.camera_club_label(tier, intrinsics, source) == label


def test_a_nominal_lens_and_a_weak_tier_say_why():
    assert labels.camera_club_note("high", "nominal", "camera_fused_chained") == (
        labels.NOMINAL_LENS_NOTE
    )
    assert "tier" in labels.camera_club_note("experimental", "calibrated", "camera_fused_chained")
    assert labels.camera_club_note("high", "calibrated", "camera_fused_chained") is None


@pytest.mark.parametrize(
    ("source", "status", "tier", "intrinsics", "label"),
    [
        ("radar", None, None, None, "measured"),
        ("radar", "azimuth_uncalibrated", None, None, "estimated"),
        ("radar", "tilt_configured", None, None, "estimated"),
        # Q3: a value that fails its own gate is experimental
        ("radar_low_coherence", None, None, None, "experimental"),
        ("camera_low_consensus", None, None, None, "experimental"),
        # Q5: a high-tier camera horizontal follows the fused club's rule
        ("camera_assisted_experimental", None, "high", "calibrated", "measured"),
        ("camera_assisted_experimental", None, "high", "nominal", "estimated"),
        ("camera_assisted_experimental", None, "experimental", "calibrated", "experimental"),
        # the camera fallback: estimated on a calibrated lens, experimental on a nominal one
        ("camera_fallback", None, None, "calibrated", "estimated"),
        ("camera_fallback", None, None, "nominal", "experimental"),
        ("camera_fallback", None, None, None, "experimental"),
        ("camera_only_experimental", None, None, "calibrated", "experimental"),
        ("estimated", None, None, None, "estimated"),
    ],
)
def test_a_launch_label_follows_the_policy(source, status, tier, intrinsics, label):
    assert labels.launch_label(source, status, camera_tier=tier, intrinsics=intrinsics) == label


def _shot(**fields):
    base = {
        "launch_angle_vertical": 15.0,
        "launch_angle_vertical_source": "radar",
        "launch_angle_horizontal": 2.0,
        "launch_angle_horizontal_source": "radar",
        "launch_angle_horizontal_status": None,
        "inclinometer": {"applied_to": ["camera", "iwr6843"]},
        "camera_lens_intrinsics": "nominal",
    }
    base.update(fields)
    return base


def test_an_iwr_vertical_on_the_configured_tilt_is_estimated_with_its_reason():
    measured = labels.shot_launch_label(_shot(), "vertical")
    no_lis3dh = labels.shot_launch_label(_shot(inclinometer=None), "vertical")
    not_applied = labels.shot_launch_label(
        _shot(inclinometer={"applied_to": ["camera"]}), "vertical"
    )

    assert measured == ("measured", None)
    assert no_lis3dh == ("estimated", labels.TILT_CONFIGURED_NOTE)
    assert not_applied == ("estimated", "tilt is the configured value, not measured")


def test_a_mock_shot_has_no_tilt_to_configure():
    assert labels.shot_launch_label(_shot(inclinometer=None, mode="mock"), "vertical")[0] == (
        "measured"
    )


def test_the_shot_label_reads_the_camera_tier_and_the_lens_from_the_shot():
    high = _shot(
        launch_angle_horizontal_source="camera_assisted_experimental",
        experimental_camera_horizontal_status="camera_assisted_high",
    )
    calibrated = dict(high, camera_lens_intrinsics="calibrated")
    weak = dict(high, experimental_camera_horizontal_status="camera_experimental_disagreement")

    assert labels.shot_launch_label(high, "horizontal") == ("estimated", labels.NOMINAL_LENS_NOTE)
    assert labels.shot_launch_label(calibrated, "horizontal") == ("measured", None)
    assert labels.shot_launch_label(weak, "horizontal")[0] == "experimental"


def test_the_shot_label_reads_a_shot_object_as_well_as_a_record():
    from datetime import datetime  # noqa: PLC0415

    from openflight.launch_monitor import Shot  # noqa: PLC0415

    shot = Shot(ball_speed_mph=120.0, club_speed_mph=85.0, timestamp=datetime(2026, 10, 25))
    shot.launch_angle_vertical = 15.0
    shot.launch_angle_vertical_source = "radar"

    assert labels.shot_launch_label(shot, "vertical") == ("estimated", labels.TILT_CONFIGURED_NOTE)


@pytest.mark.parametrize(
    ("current", "candidate", "wins"),
    [
        # Q5: no sigma on either side: the radar wins
        (("measured", None, True), ("measured", None, False), False),
        (("estimated", None, True), ("measured", None, False), False),
        # an experimental value never outranks a measured (or any non-experimental) one
        (("measured", None, True), ("experimental", 0.1, False), False),
        (("estimated", None, True), ("experimental", 0.1, False), False),
        (("experimental", None, True), ("estimated", None, False), True),
        # attached sigmas decide, not labels
        (("estimated", 2.0, True), ("measured", 1.0, False), True),
        (("measured", 1.0, True), ("estimated", 0.5, False), True),
        (("measured", 0.5, True), ("estimated", 1.0, False), False),
        (("experimental", None, True), ("experimental", 1.8, False), False),
        # nothing shown yet: any value is shown
        (None, ("experimental", None, False), True),
        # a value that failed its own gate never outranks one that passed
        (("experimental", None, True, True), ("experimental", None, False), True),
        (("experimental", None, False), ("experimental", None, True, True), False),
        (("experimental", 5.0, False), ("experimental", 0.1, True, True), False),
        # both failed: the rules above decide
        (("experimental", None, True, True), ("experimental", None, False, True), False),
    ],
)
def test_a_horizontal_is_chosen_by_sigma_then_the_radar_and_never_x_over_non_x(
    current, candidate, wins
):
    def option(spec):
        if spec is None:
            return None
        label, sigma, radar, *failed = spec
        return labels.HorizontalOption(
            label=label, sigma_deg=sigma, radar=radar, failed_gate=bool(failed and failed[0])
        )

    assert labels.prefers_horizontal(option(candidate), over=option(current)) is wins


def test_the_iwr_partial_label_maps_to_estimated_for_every_record():
    assert labels.LABEL_IWR_PARTIAL == "iwr_partial"
    assert labels.record_label(labels.LABEL_IWR_PARTIAL) == "estimated"
    assert labels.record_label("measured") == "measured"
    assert labels.record_label("experimental") == "experimental"


def test_an_ambiguous_iwr_track_makes_its_launch_angles_estimated():
    shot = _shot(iwr6843_launch_status="accepted_track_ambiguous")

    vertical = labels.shot_launch_label(shot, "vertical")
    horizontal = labels.shot_launch_label(shot, "horizontal")

    assert vertical[0] == horizontal[0] == "estimated"
    assert "accepted_track_ambiguous" in vertical[1]


@pytest.mark.parametrize(
    ("status", "notes"),
    [
        ("accepted_iwr_partial", [labels.IWR_PARTIAL_NOTE]),
        (
            "accepted_track_ambiguous_iwr_partial",
            [labels.IWR_PARTIAL_NOTE, "accepted_track_ambiguous"],
        ),
    ],
)
def test_a_launch_from_a_partial_dump_is_estimated_on_any_range(status, notes):
    # the range here is the camera prior's, so only the launch status names the partial dump
    shot = _shot(iwr6843_launch_status=status, ball_range_source="camera_prior")

    for axis in ("vertical", "horizontal"):
        label, note = labels.shot_launch_label(shot, axis)
        assert label == "estimated"
        assert all(part in note for part in notes)


@pytest.mark.parametrize(
    ("range_label", "launch"),
    [("estimated", "estimated"), ("iwr_partial", "estimated"), ("measured", "measured")],
)
def test_a_radar_launch_on_a_weaker_iwr_range_takes_its_label(range_label, launch):
    shot = _shot(ball_range_source="iwr_post_impact", ball_range_label=range_label)

    assert labels.shot_launch_label(shot, "vertical")[0] == launch
    assert labels.shot_launch_label(shot, "horizontal")[0] == launch


# ---------------------------------------------------------------- the radar vertical (30 Oct 2026)


@pytest.mark.parametrize(
    ("range_source", "range_label", "launch", "note"),
    [
        ("camera_prior", "estimated", "estimated", labels.CAMERA_PRIOR_NOTE),
        ("camera_ball_size", "experimental", "experimental", labels.BALL_SIZE_RANGE_NOTE),
        ("camera_ball_size", "estimated", "estimated", labels.BALL_SIZE_RANGE_CALIBRATED_NOTE),
    ],
)
def test_a_radar_launch_on_a_camera_range_takes_the_range_s_label(
    range_source, range_label, launch, note
):
    # Oct 27 shot 2: radar 35.87 on the camera prior's 3.217 m read "measured" in the chain
    # and the collision input while the published metric said "estimated"
    shot = _shot(ball_range_source=range_source, ball_range_label=range_label)

    for axis in ("vertical", "horizontal"):
        label, why = labels.shot_launch_label(shot, axis)
        assert label == launch, axis
        assert note in why


def test_the_range_label_is_one_function_for_the_launch_and_the_metrics():
    from openflight import shot_metrics  # noqa: PLC0415

    for source, label in (
        ("camera_prior", "estimated"),
        ("camera_ball_size", "experimental"),
        ("camera_ball_size", "estimated"),
        ("iwr_post_impact", "iwr_partial"),
        ("iwr_post_impact", "estimated"),
        ("iwr_post_impact", "measured"),
        (None, None),
    ):
        shot = _shot(ball_range_source=source, ball_range_label=label)
        assert shot_metrics._range_source_label(shot) == (  # pylint: disable=protected-access
            labels.range_source_label(shot)
        )


def test_a_camera_launch_does_not_take_the_range_label_here():
    shot = _shot(launch_angle_vertical_source="camera_fallback", ball_range_source="camera_prior")

    assert labels.CAMERA_PRIOR_NOTE not in (labels.shot_launch_label(shot, "vertical")[1] or "")


SHOT_3_FALLBACK = {"status": "accepted", "vertical_deg": 18.58, "vertical_sd_deg": 2.5}
SHOT_4_FALLBACK = {"status": "accepted", "vertical_deg": 21.52, "vertical_sd_deg": 3.13}


def test_a_radar_vertical_beyond_three_sigma_of_the_camera_reads_experimental():
    # Oct 27 shot 3: radar 29.99 against an accepted camera 18.58 +- 2.5 (4.6 sigma)
    shot = _shot(launch_angle_vertical=29.99, camera_fallback_launch=SHOT_3_FALLBACK)

    label, why = labels.shot_launch_label(shot, "vertical")

    assert label == "experimental"
    assert "29.99" in why and "18.58" in why and "4.6 sigma" in why
    # the horizontal is not the vertical's business
    assert labels.shot_launch_label(shot, "horizontal")[0] == "measured"


def test_a_radar_vertical_within_three_sigma_keeps_its_label():
    # Oct 27 shot 4: radar 28.50 against an accepted camera 21.52 +- 3.13 (2.2 sigma)
    shot = _shot(launch_angle_vertical=28.50, camera_fallback_launch=SHOT_4_FALLBACK)

    assert labels.shot_launch_label(shot, "vertical") == ("measured", None)


@pytest.mark.parametrize(
    "fallback",
    [
        {**SHOT_3_FALLBACK, "status": "withheld_residual"},
        {**SHOT_3_FALLBACK, "vertical_sd_deg": None},
        {**SHOT_3_FALLBACK, "vertical_sd_deg": 0.0},
        {**SHOT_3_FALLBACK, "vertical_deg": None},
        None,
    ],
    ids=["not-accepted", "no-sigma", "zero-sigma", "no-value", "no-fallback"],
)
def test_without_an_accepted_camera_vertical_and_its_sigma_there_is_no_check(fallback):
    shot = _shot(launch_angle_vertical=29.99, camera_fallback_launch=fallback)

    assert labels.shot_launch_label(shot, "vertical") == ("measured", None)


def test_the_check_takes_the_fallback_it_is_given_over_the_shot_s():
    shot = _shot(launch_angle_vertical=29.99)

    label, _why = labels.shot_launch_label(shot, "vertical", camera_fallback=SHOT_3_FALLBACK)

    assert label == "experimental"


@pytest.mark.parametrize(
    ("radar", "radar_sd", "camera", "camera_sd", "sigmas"),
    [
        (29.99, None, 18.58, 2.5, 4.564),
        (28.50, None, 21.52, 3.13, None),
        # the radar's own sigma, when the measurement carries one, joins the camera's
        (29.99, 3.0, 18.58, 2.5, None),
        (10.0, None, 18.0, 2.0, 4.0),
    ],
)
def test_the_disagreement_counts_combined_sigmas(radar, radar_sd, camera, camera_sd, sigmas):
    found = labels.vertical_disagreement(radar, camera, camera_sd, radar_sd_deg=radar_sd)

    if sigmas is None:
        assert found is None
    else:
        assert found == pytest.approx(sigmas, abs=1e-3)
    assert labels.VERTICAL_DISAGREEMENT_SIGMAS == 3.0
