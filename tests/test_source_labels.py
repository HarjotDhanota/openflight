"""A Shot's source text names its value's label; the predicates read it back."""

import pytest

from openflight import source_labels as labels


@pytest.mark.parametrize(
    ("source", "calculated", "model_geometry", "estimated"),
    [
        ("calculated (D-plane tilt)", True, False, False),
        ("model geometry (D-plane tilt; club path from the arc prior)", False, True, False),
        ("estimated (D-plane tilt; launch direction weak)", False, False, True),
        ("radar", False, False, False),
        ("", False, False, False),
        (None, False, False, False),
    ],
)
def test_the_predicates_read_the_label_from_the_source_text(
    source, calculated, model_geometry, estimated
):
    assert labels.is_calculated_source(source) is calculated
    assert labels.is_model_geometry_source(source) is model_geometry
    assert labels.is_estimated_source(source) is estimated


@pytest.mark.parametrize(
    ("source", "measured"),
    [("measured (camera)", True), ("calculated (D-plane tilt)", False), ("", False), (None, False)],
)
def test_a_measured_source_is_read_back(source, measured):
    assert labels.is_measured_source(source) is measured


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
    ],
)
def test_a_horizontal_is_chosen_by_sigma_then_the_radar_and_never_x_over_non_x(
    current, candidate, wins
):
    def option(spec):
        if spec is None:
            return None
        label, sigma, radar = spec
        return labels.HorizontalOption(label=label, sigma_deg=sigma, radar=radar)

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
    ("range_label", "launch"),
    [("estimated", "estimated"), ("iwr_partial", "estimated"), ("measured", "measured")],
)
def test_a_radar_launch_on_a_weaker_iwr_range_takes_its_label(range_label, launch):
    shot = _shot(ball_range_source="iwr_post_impact", ball_range_label=range_label)

    assert labels.shot_launch_label(shot, "vertical")[0] == launch
    assert labels.shot_launch_label(shot, "horizontal")[0] == launch
