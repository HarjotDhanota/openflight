"""E2 collision model: D-plane inversion with PING weights, Penner spin, spin axis, gyro.

Published numbers checked here come from the papers themselves:

- Wood, Henrikson & Broadie 2018, Proceedings 2(6):249, Table 1
  (doi:10.3390/proceedings2060249).
- Penner 2001, Am. J. Phys. 69, 563, Eqs. (5), (7), (8), (10), (11) and Fig. 2
  (doi:10.1119/1.1344164). Fig. 2 has no printed table: the values below were read
  off the published figure by pixel digitisation at 400 dpi (see
  docs/how-it-works/collision-model.md), so they carry a reading tolerance.
- Cross & Nathan 2007, Am. J. Phys. 75, 658, Eq. (6) and its worked value
  (doi:10.1119/1.2713788).
"""

import math

import pytest

from openflight import collision_model as cm
from openflight.clubs import ClubType
from openflight.metric_definitions import d_plane_tilt_deg, spin_loft_deg

M = cm.LABEL_MEASURED


def _inp(value, label=M, source="test", frame="target_line"):
    return cm.Input(value=value, label=label, source=source, frame=frame)


def _evaluate(
    *,
    club=ClubType.IRON_7,
    path=-2.0,
    attack=-4.0,
    launch_h=1.0,
    launch_v=18.0,
    club_speed=85.0,
    labels=None,
    frames=None,
):
    labels = labels or {}
    frames = frames or {}
    return cm.evaluate(
        club=club,
        club_path=_inp(path, labels.get("path", M), frame=frames.get("path", "target_line")),
        attack_angle=_inp(attack, labels.get("attack", M), frame=None),
        launch_horizontal=_inp(
            launch_h, labels.get("launch_h", M), frame=frames.get("launch_h", "target_line")
        ),
        launch_vertical=_inp(launch_v, labels.get("launch_v", M), frame=None),
        club_speed_mph=_inp(club_speed, labels.get("club_speed", M), frame=None),
    )


# ---- published weights (Wood et al. 2018, Table 1) ------------------------------------


def test_published_ping_weights_are_the_papers_table_1():
    assert cm.PING_2018_ANCHORS == (
        ("driver", 0.76, 0.83),
        ("7-iron", 0.69, 0.81),
        ("58-degree wedge", 0.61, 0.72),
    )


@pytest.mark.parametrize(
    ("club", "horizontal", "vertical"),
    [
        (ClubType.DRIVER, 0.76, 0.83),
        (ClubType.IRON_7, 0.69, 0.81),
        (ClubType.LW, 0.61, 0.72),  # the fork's LW is 58 deg, the paper's wedge
    ],
)
def test_each_anchor_club_gets_its_category_weights_exactly(club, horizontal, vertical):
    weights = cm.weights_for_club(club)

    assert weights.horizontal == pytest.approx(horizontal)
    assert weights.vertical == pytest.approx(vertical)


def test_clubs_between_anchors_interpolate_linearly_in_nominal_loft():
    # 3-wood 15 deg sits 4.5/23.5 of the way from the driver (10.5) to the 7-iron (34)
    three_wood = cm.weights_for_club(ClubType.WOOD_3)
    t = (15.0 - 10.5) / (34.0 - 10.5)
    assert three_wood.horizontal == pytest.approx(0.76 + t * (0.69 - 0.76))
    assert three_wood.vertical == pytest.approx(0.83 + t * (0.81 - 0.83))
    # PW 46 deg is half way from the 7-iron (34) to the 58 deg wedge
    pw = cm.weights_for_club(ClubType.PW)
    assert pw.horizontal == pytest.approx(0.65)
    assert pw.vertical == pytest.approx(0.765)
    assert "interpolated" in pw.basis


def test_weights_never_rise_with_loft_and_are_clamped_outside_the_anchors():
    """Both papers: the ratio falls as obliqueness (loft) rises; no extrapolation."""
    known = [club for club in ClubType if club is not ClubType.UNKNOWN]
    ordered = sorted(known, key=lambda club: cm.club_nominal_loft_deg(club))
    pairs = [cm.weights_for_club(club) for club in ordered]
    for low, high in zip(pairs, pairs[1:]):
        assert high.horizontal <= low.horizontal + 1e-12
        assert high.vertical <= low.vertical + 1e-12
    assert cm.weights_for_loft(5.0).horizontal == pytest.approx(0.76)
    assert cm.weights_for_loft(64.0).horizontal == pytest.approx(0.61)


def test_an_unknown_club_has_no_category_and_no_weights():
    assert cm.weights_for_club(ClubType.UNKNOWN) is None


# ---- inversion ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("face", "loft", "path", "attack", "club"),
    [
        (2.0, 14.0, -1.0, 3.0, ClubType.DRIVER),
        (-3.5, 30.0, 4.0, -4.5, ClubType.IRON_7),
        (0.5, 52.0, -6.0, -6.0, ClubType.SW),
        (8.0, 20.0, 8.0, -2.0, ClubType.HYBRID_5),
    ],
)
def test_inversion_round_trips_a_forward_simulated_launch(face, loft, path, attack, club):
    weights = cm.weights_for_club(club)
    launch_h, launch_v = cm.forward_launch(
        face_angle_deg=face,
        dynamic_loft_deg=loft,
        club_path_deg=path,
        attack_angle_deg=attack,
        weights=weights,
    )

    got_face, got_loft = cm.invert_d_plane(
        launch_horizontal_deg=launch_h,
        launch_vertical_deg=launch_v,
        club_path_deg=path,
        attack_angle_deg=attack,
        weights=weights,
    )

    assert got_face == pytest.approx(face, abs=1e-12)
    assert got_loft == pytest.approx(loft, abs=1e-12)


def test_the_launch_is_the_published_fraction_of_the_way_from_club_to_face():
    weights = cm.weights_for_club(ClubType.DRIVER)
    launch_h, launch_v = cm.forward_launch(
        face_angle_deg=4.0,
        dynamic_loft_deg=14.0,
        club_path_deg=0.0,
        attack_angle_deg=4.0,
        weights=weights,
    )

    assert launch_h == pytest.approx(0.76 * 4.0)
    assert launch_v == pytest.approx(4.0 + 0.83 * 10.0)


def test_2d_limit_face_equals_path_gives_no_tilt_and_spin_loft_loft_minus_attack():
    result = _evaluate(path=-2.0, launch_h=-2.0, attack=-4.0, launch_v=18.0)
    face = result.metrics["face_angle"].value
    loft = result.metrics["dynamic_loft"].value

    assert face == pytest.approx(-2.0)
    assert result.metrics["face_to_path"].value == pytest.approx(0.0)
    assert result.metrics["d_plane_tilt"].value == pytest.approx(0.0, abs=1e-9)
    assert result.metrics["spin_loft"].value == pytest.approx(loft - (-4.0))


def test_2d_limit_loft_equals_attack_leaves_only_the_horizontal_obliqueness():
    # launch_v == attack means the face's loft equals the attack angle
    result = _evaluate(path=0.0, launch_h=3.0, attack=2.0, launch_v=2.0)

    assert result.metrics["dynamic_loft"].value == pytest.approx(2.0)
    face = result.metrics["face_angle"].value
    # with loft == attack, the 3D spin loft is the horizontal angle at that elevation
    expected = math.degrees(
        math.acos(
            math.cos(math.radians(2.0)) ** 2 * math.cos(math.radians(face))
            + math.sin(math.radians(2.0)) ** 2
        )
    )
    assert result.metrics["spin_loft"].value == pytest.approx(expected)


def test_a_weight_of_one_makes_the_face_the_launch_direction():
    weights = cm.LaunchWeights(horizontal=1.0, vertical=1.0, category="test", basis="test")

    face, loft = cm.invert_d_plane(
        launch_horizontal_deg=2.5,
        launch_vertical_deg=15.0,
        club_path_deg=-3.0,
        attack_angle_deg=-5.0,
        weights=weights,
    )

    assert face == pytest.approx(2.5)
    assert loft == pytest.approx(15.0)


# ---- signs --------------------------------------------------------------------------


def test_a_launch_right_of_path_means_face_right_of_path_and_a_fade_axis():
    result = _evaluate(path=-2.0, launch_h=1.0)

    assert result.metrics["face_angle"].value > 1.0  # beyond the launch, away from path
    assert result.metrics["face_to_path"].value > 0.0
    assert result.metrics["d_plane_tilt"].value > 0.0
    assert result.metrics["spin_axis"].value > 0.0  # TrackMan: + curves right


def test_a_launch_left_of_path_means_face_left_of_path_and_a_draw_axis():
    result = _evaluate(path=3.0, launch_h=1.0)

    assert result.metrics["face_angle"].value < 1.0
    assert result.metrics["face_to_path"].value < 0.0
    assert result.metrics["d_plane_tilt"].value < 0.0
    assert result.metrics["spin_axis"].value < 0.0


def test_dynamic_loft_sits_above_a_launch_that_sits_above_the_attack():
    result = _evaluate(attack=-4.0, launch_v=18.0)

    assert result.metrics["dynamic_loft"].value > 18.0


def test_spin_loft_and_tilt_are_the_d37_functions_of_the_recovered_face_and_loft():
    result = _evaluate()
    face = result.metrics["face_angle"].value
    loft = result.metrics["dynamic_loft"].value

    assert result.metrics["spin_loft"].value == pytest.approx(spin_loft_deg(loft, face, -4.0, -2.0))
    assert result.metrics["d_plane_tilt"].value == pytest.approx(
        d_plane_tilt_deg(loft, face, -4.0, -2.0)
    )


# ---- Penner 2001 collision model ----------------------------------------------------

# Read off Penner 2001 Fig. 2 (v_ci = 45 m/s, M = 200 g): loft -> (launch speed m/s,
# launch angle deg, initial spin rpm).
PENNER_FIG2 = {
    6.0: (62.95, 4.90, 1415.0),
    10.0: (62.41, 8.16, 2335.0),
    15.0: (61.37, 12.11, 3474.0),
    25.0: (58.05, 20.02, 5628.0),
    29.0: (56.24, 23.07, 6441.0),
}


@pytest.mark.parametrize("loft", sorted(PENNER_FIG2))
def test_penner_impact_reproduces_the_papers_figure_2(loft):
    speed, angle, spin = PENNER_FIG2[loft]

    impact = cm.penner_impact(45.0, loft)

    assert impact.launch_speed_m_s == pytest.approx(speed, abs=0.15)
    assert impact.launch_angle_from_club_deg == pytest.approx(angle, abs=0.15)
    assert impact.spin_rpm == pytest.approx(spin, rel=0.02)


def test_penner_constants_are_the_papers_stated_values():
    assert cm.PENNER_BALL_MASS_KG == pytest.approx(0.0459)
    assert cm.PENNER_BALL_DIAMETER_M == pytest.approx(0.0427)
    assert cm.PENNER_HEAD_MASS_KG == pytest.approx(0.200)
    impact = cm.penner_impact(45.0, 10.0)
    assert impact.restitution == pytest.approx(0.86 - 0.0029 * 45.0 * math.cos(math.radians(10.0)))


def test_penner_spin_is_the_rolling_condition_and_does_not_depend_on_restitution():
    impact = cm.penner_impact(40.0, 30.0)
    r = cm.PENNER_BALL_DIAMETER_M / 2.0
    m_over_m = cm.PENNER_BALL_MASS_KG / cm.PENNER_HEAD_MASS_KG

    v_parallel = 40.0 * math.sin(math.radians(30.0)) / (1.0 + m_over_m + 2.5)
    assert impact.spin_rad_s == pytest.approx(v_parallel / (0.4 * r))


def test_calculated_spin_scales_with_club_speed_and_spin_loft():
    slow = _evaluate(club_speed=70.0).metrics["spin_rate"].value
    fast = _evaluate(club_speed=90.0).metrics["spin_rate"].value
    lofted = _evaluate(club_speed=70.0, launch_v=26.0).metrics["spin_rate"].value

    assert fast == pytest.approx(slow * 90.0 / 70.0)
    assert lofted > slow


def test_a_spin_loft_beyond_the_rolling_regime_is_not_calculated():
    # Coulomb friction: rolling needs mu >= v_parallel / v_normal; the lowest published
    # dry ball-face value is 0.35 (Henrikson et al. 2020)
    metric = cm.calculated_spin(
        club_speed=_inp(60.0),
        spin_loft=cm.Metric(value=70.0, status=cm.STATUS_CALCULATED, source="x", unit="deg"),
    )

    assert metric.value is None
    assert metric.status == cm.STATUS_NOT_MEASURED
    assert "rolling" in metric.reason


# ---- spin axis and gear effect ------------------------------------------------------


def test_without_an_impact_location_the_spin_axis_is_the_d_plane_tilt_with_the_note():
    result = _evaluate(club=ClubType.DRIVER, launch_v=13.0, attack=2.0)

    axis = result.metrics["spin_axis"]
    assert axis.value == pytest.approx(result.metrics["d_plane_tilt"].value)
    assert axis.note == "no impact location: gear effect not applied"
    assert axis.status == cm.STATUS_CALCULATED


def test_cross_nathan_eq6_reproduces_the_papers_worked_value():
    """Eq. (6): -7.6 rad/s for their golf ball on a 210 g block at b = 50 mm.

    Ball 46.1 g (with hook), diameter 42.7 mm; block 120 x 69 mm in plan, struck on
    its 120 mm face, so d = 69/2 mm and I_B = M (L^2 + W^2) / 12; v_y1 = 0.82 m/s;
    e = 0.83 (the head-on value the paper measured).
    """
    block_mass = 0.210
    moi = block_mass * (0.120**2 + 0.069**2) / 12.0

    omega = cm.cross_nathan_gear_spin_rad_s(
        normal_speed_m_s=0.82,
        restitution=0.83,
        cg_depth_m=0.0345,
        impact_offset_m=0.050,
        head_moi_kg_m2=moi,
        head_mass_kg=block_mass,
        ball_mass_kg=0.0461,
        ball_radius_m=0.0427 / 2.0,
    )

    assert omega == pytest.approx(-7.6, abs=0.1)


@pytest.mark.parametrize(("offset", "depth"), [(0.0, 0.0345), (0.05, 0.0)])
def test_no_gear_effect_on_centre_or_with_the_cg_on_the_face(offset, depth):
    omega = cm.cross_nathan_gear_spin_rad_s(
        normal_speed_m_s=40.0,
        restitution=0.8,
        cg_depth_m=depth,
        impact_offset_m=offset,
        head_moi_kg_m2=4e-4,
        head_mass_kg=0.2,
    )

    assert omega == pytest.approx(0.0)


def test_a_gear_sidespin_tilts_the_axis_from_the_d_plane_tilt():
    tilt = cm.Metric(value=0.0, status=cm.STATUS_CALCULATED, source="D-plane", unit="deg")
    spin = cm.Metric(value=3000.0, status=cm.STATUS_CALCULATED, source="Penner", unit="rpm")

    axis = cm.spin_axis(tilt, spin, gear_sidespin_rpm=300.0)

    assert axis.value == pytest.approx(math.degrees(math.atan2(300.0, 3000.0)))
    assert "gear effect" in axis.source


# ---- gyro ---------------------------------------------------------------------------


def test_gyro_is_not_measured_because_the_planar_model_has_none():
    gyro = _evaluate().metrics["gyro"]

    assert gyro.value is None
    assert gyro.status == cm.STATUS_NOT_MEASURED
    assert "D-plane" in gyro.reason


# ---- labels and missing inputs ------------------------------------------------------


def test_every_output_is_labelled_calculated_with_its_source():
    result = _evaluate()

    for name in ("face_angle", "dynamic_loft"):
        assert result.metrics[name].status == cm.STATUS_CALCULATED
        assert result.metrics[name].source == "D-plane inversion, Wood et al. 2018 weights"
    assert "Penner 2001" in result.metrics["spin_rate"].source
    for name in ("spin_loft", "face_to_path", "d_plane_tilt", "spin_rate", "spin_axis"):
        assert result.metrics[name].status == cm.STATUS_CALCULATED


def test_a_model_geometry_path_makes_every_value_built_on_it_model_geometry():
    result = _evaluate(labels={"path": cm.LABEL_MODEL_GEOMETRY})

    for name in (
        "face_angle",
        "face_to_path",
        "spin_loft",
        "d_plane_tilt",
        "spin_rate",
        "spin_axis",
    ):
        assert result.metrics[name].status == cm.STATUS_MODEL_GEOMETRY, name
    # dynamic loft uses only the vertical inputs, which are measured
    assert result.metrics["dynamic_loft"].status == cm.STATUS_CALCULATED


@pytest.mark.parametrize(
    ("missing", "not_measured", "still_calculated"),
    [
        (
            "path",
            {"face_angle", "face_to_path", "spin_loft", "d_plane_tilt", "spin_rate", "spin_axis"},
            {"dynamic_loft"},
        ),
        (
            "launch_h",
            {"face_angle", "face_to_path", "spin_loft", "d_plane_tilt", "spin_rate", "spin_axis"},
            {"dynamic_loft"},
        ),
        (
            "attack",
            {"dynamic_loft", "spin_loft", "d_plane_tilt", "spin_rate", "spin_axis"},
            {"face_angle", "face_to_path"},
        ),
        (
            "launch_v",
            {"dynamic_loft", "spin_loft", "d_plane_tilt", "spin_rate", "spin_axis"},
            {"face_angle", "face_to_path"},
        ),
        (
            "club_speed",
            {"spin_rate"},
            {"face_angle", "dynamic_loft", "spin_loft", "d_plane_tilt", "spin_axis"},
        ),
    ],
)
def test_a_missing_input_means_not_measured_never_a_filled_in_value(
    missing, not_measured, still_calculated
):
    kwargs = {missing: None}
    result = _evaluate(**kwargs)

    for name in not_measured:
        metric = result.metrics[name]
        assert metric.value is None, name
        assert metric.status == cm.STATUS_NOT_MEASURED, name
        assert metric.reason, name
    for name in still_calculated:
        assert result.metrics[name].value is not None, name


def test_an_unknown_club_has_no_weights_so_face_and_loft_are_not_measured():
    result = _evaluate(club=ClubType.UNKNOWN)

    assert result.metrics["face_angle"].value is None
    assert "club category" in result.metrics["face_angle"].reason


def test_a_non_finite_input_is_a_missing_input():
    result = _evaluate(launch_h=math.nan)

    assert result.metrics["face_angle"].value is None
    assert result.metrics["face_angle"].status == cm.STATUS_NOT_MEASURED


def test_horizontal_metrics_carry_the_frame_of_their_inputs_and_mixed_is_named():
    same = _evaluate()
    assert same.metrics["face_angle"].frame == "target_line"
    assert same.metrics["spin_axis"].frame == "target_line"
    assert same.metrics["dynamic_loft"].frame is None

    mixed = _evaluate(frames={"path": "unit_boresight", "launch_h": "target_line"})
    assert mixed.metrics["face_angle"].frame == "mixed"
    assert mixed.metrics["face_angle"].value is not None
    assert "frames" in mixed.metrics["face_angle"].note


def test_the_result_serialises_every_metric_with_status_source_and_frame():
    payload = _evaluate().to_dict()

    assert payload["model_version"] == cm.MODEL_VERSION
    assert set(payload["metrics"]) == {
        "face_angle",
        "dynamic_loft",
        "spin_loft",
        "face_to_path",
        "d_plane_tilt",
        "spin_rate",
        "spin_axis",
        "gyro",
    }
    for metric in payload["metrics"].values():
        assert set(metric) >= {"value", "status", "source", "unit", "frame", "reason", "note"}
    assert payload["weights"]["horizontal"] == pytest.approx(0.69)
    assert payload["inputs"]["club_path"]["label"] == "measured"


def test_the_spin_axis_source_strings_name_the_model():
    assert cm.SPIN_AXIS_SOURCE_TILT == "calculated (D-plane tilt, Wood et al. 2018 weights)"
    assert cm.SPIN_AXIS_SOURCE_GEAR == "calculated (D-plane + gear effect, Cross & Nathan 2007)"
    tilt = cm.Metric(value=2.0, status=cm.STATUS_CALCULATED, source="D-plane", unit="deg")
    spin = cm.Metric(value=3000.0, status=cm.STATUS_CALCULATED, source="Penner", unit="rpm")
    assert cm.shot_spin_axis_source(cm.spin_axis(tilt, spin)) == cm.SPIN_AXIS_SOURCE_TILT
    assert (
        cm.shot_spin_axis_source(cm.spin_axis(tilt, spin, gear_sidespin_rpm=100.0))
        == cm.SPIN_AXIS_SOURCE_GEAR
    )
