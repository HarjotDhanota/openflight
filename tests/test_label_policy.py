"""The owner's metric-label policy (25 Oct 2026), end to end through the kiosk's chain.

Weakest input wins: measured (a sensor saw it, gates passed, geometry measured or
calibrated), calculated (a model on M/C inputs; its own assumptions only noted),
model_geometry, estimated (a nominal or assumed input), experimental (a failed or
skipped gate, or an experimental input; never sent to a simulator). The fixes and their
numbers are those of reports/chain-verification-2026-10-25.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server as server_module, shot_models, source_chain, source_labels as labels
from openflight.camera import fallback_chain as chain
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot

FALLBACK = {
    "status": "accepted",
    "vertical_deg": 15.4,
    "vertical_sd_deg": 1.3,
    "horizontal_deg": 1.6,
    "horizontal_sd_deg": 1.8,
    "n_points": 9,
}


def _shot(**values):
    values.setdefault("ball_speed_mph", 118.0)
    values.setdefault("club_speed_mph", 88.0)
    values.setdefault("timestamp", datetime(2026, 10, 25, 9, 0, 0))
    values.setdefault("club", ClubType.IRON_7)
    return Shot(**values)


@pytest.fixture(name="azimuth_calibrated")
def fixture_azimuth_calibrated(monkeypatch):
    monkeypatch.setattr(
        server_module, "iwr6843_runtime", SimpleNamespace(horizontal_phase_reference_rad=0.1)
    )


# ---------------------------------------------------------------- 4: horizontal ranking


def test_a_camera_assisted_horizontal_never_outranks_a_measured_radar_one(azimuth_calibrated):
    for status, intrinsics in (
        ("camera_assisted_high", "calibrated"),
        ("camera_assisted_high", "nominal"),
        ("camera_assisted_experimental_agreement", "calibrated"),
    ):
        shot = _shot(
            launch_angle_horizontal=2.0,
            launch_angle_horizontal_source="radar",
            camera_lens_intrinsics=intrinsics,
        )
        shot.experimental_camera_horizontal_status = status

        server_module._apply_camera_horizontal_decision(  # pylint: disable=protected-access
            shot, 1.7, 0.75, "camera_assisted_experimental"
        )

        # no sigma on either: the radar wins (Q5)
        assert (shot.launch_angle_horizontal, shot.launch_angle_horizontal_source) == (
            2.0,
            "radar",
        ), (status, intrinsics)


def test_a_camera_horizontal_replaces_a_low_coherence_radar_one_unless_it_is_experimental(
    azimuth_calibrated,
):
    shot = _shot(launch_angle_horizontal=2.0, launch_angle_horizontal_source="radar_low_coherence")
    shot.experimental_camera_horizontal_status = "camera_assisted_high"
    server_module._apply_camera_horizontal_decision(  # pylint: disable=protected-access
        shot, 1.7, 0.75, "camera_assisted_experimental"
    )
    assert shot.launch_angle_horizontal_source == "camera_assisted_experimental"

    weak = _shot(launch_angle_horizontal=2.0, launch_angle_horizontal_source="radar_low_coherence")
    server_module._apply_camera_horizontal_decision(  # pylint: disable=protected-access
        weak, 6.0, 0.15, "camera_low_consensus"
    )
    # both failed their own gate, no sigma: the radar's stays
    assert weak.launch_angle_horizontal_source == "radar_low_coherence"

    nominal = _shot(
        launch_angle_horizontal=43.8, launch_angle_horizontal_source="radar_low_coherence"
    )
    nominal.experimental_camera_horizontal_status = "camera_assisted_experimental_agreement"
    server_module._apply_camera_horizontal_decision(  # pylint: disable=protected-access
        nominal, 9.5, 0.6, "camera_assisted_experimental"
    )
    # experimental against experimental, but only the radar's failed its gate
    assert (nominal.launch_angle_horizontal, nominal.launch_angle_horizontal_source) == (
        9.5,
        "camera_assisted_experimental",
    )


def test_with_no_radar_horizontal_the_camera_one_is_shown(azimuth_calibrated):
    shot = _shot()
    server_module._apply_camera_horizontal_decision(  # pylint: disable=protected-access
        shot, 1.7, 0.75, "camera_assisted_experimental"
    )
    assert shot.launch_angle_horizontal == 1.7


def test_an_accepted_camera_fallback_outranks_a_radar_horizontal_that_failed_its_gate():
    shot = _shot(launch_angle_horizontal=3.2, launch_angle_horizontal_source="radar_low_coherence")

    resolved = chain.launch_resolutions(shot, FALLBACK)["launch_horizontal"]

    # both experimental on a nominal lens, but only the radar's failed its own gate
    # (Oct 27 shot 4: +43.8 low-coherence radar over an accepted 9.5 camera -> face +55)
    assert (resolved.source, resolved.label) == ("camera_fallback", "experimental")


def test_through_a_calibrated_lens_the_fallback_outranks_an_experimental_radar_horizontal():
    shot = _shot(
        launch_angle_horizontal=3.2,
        launch_angle_horizontal_source="radar_low_coherence",
        camera_lens_intrinsics="calibrated",
    )

    resolved = chain.launch_resolutions(shot, FALLBACK)["launch_horizontal"]

    assert (resolved.source, resolved.label, resolved.uncertainty) == (
        "camera_fallback",
        "estimated",
        1.8,
    )


def test_a_measured_or_estimated_radar_horizontal_stands_before_the_fallback():
    for status in (None, "azimuth_uncalibrated"):
        shot = _shot(
            launch_angle_horizontal=2.0,
            launch_angle_horizontal_source="radar",
            launch_angle_horizontal_status=status,
            camera_lens_intrinsics="calibrated",
        )
        resolved = chain.launch_resolutions(shot, FALLBACK)["launch_horizontal"]
        assert resolved.source == "radar", status


def test_without_a_sensor_horizontal_the_fallback_gives_it_under_the_lens_label():
    nominal = chain.launch_resolutions(_shot(), FALLBACK)
    calibrated = chain.launch_resolutions(_shot(camera_lens_intrinsics="calibrated"), FALLBACK)

    assert nominal["launch_horizontal"].label == "experimental"
    assert nominal["launch_vertical"].label == "experimental"
    assert calibrated["launch_horizontal"].label == "estimated"
    assert calibrated["launch_vertical"].label == "estimated"


# ---------------------------------------------------------------- 1: camera club labels


def _club_shot(path_tier="high", attack_tier="high", status="chained_high", intrinsics="nominal"):
    shot = _shot(
        launch_angle_vertical=16.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=2.0,
        launch_angle_horizontal_source="radar",
        launch_angle_horizontal_confidence=0.8,
        launch_angle_horizontal_frame="target_line",
        inclinometer={"applied_to": ["camera", "iwr6843"]},
        camera_lens_intrinsics=intrinsics,
        spin_rpm=6800.0,
        spin_confidence=0.85,
        spin_method="ops",
    )
    shot.experimental_fused_club_path_deg = 1.0
    shot.experimental_fused_attack_angle_deg = -4.0
    shot.experimental_fused_club_path_frame = "target_line"
    shot.experimental_fused_status = status
    shot.experimental_fused_club_path_confidence = path_tier
    shot.experimental_fused_attack_angle_confidence = attack_tier
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access
    return shot


def _inputs(shot):
    inputs = shot.collision_model["inputs"]
    return {
        name: (inputs[name]["label"], inputs[name].get("experimental"))
        for name in ("club_path", "attack_angle")
    }


def _statuses(shot, *names):
    metrics = shot.to_dict()["metrics"]
    return tuple(metrics[name]["status"] for name in names)


@pytest.mark.parametrize(
    ("path_tier", "status", "intrinsics", "inputs", "records"),
    [
        ("high", "chained_high", "calibrated", ("measured", False), ("measured", "measured")),
        ("high", "chained_high", "nominal", ("estimated", False), ("estimated", "estimated")),
        ("experimental", "chained_high", "calibrated", ("estimated", True), ("experimental",) * 2),
        ("low", "chained_high", "calibrated", ("estimated", True), ("experimental",) * 2),
        ("high", "camera_ops_fallback", "calibrated", ("estimated", True), ("experimental",) * 2),
    ],
    ids=["high-calibrated", "high-nominal", "experimental-tier", "low-tier", "fused-ops"],
)
def test_the_collision_input_and_the_record_take_one_camera_club_label(
    path_tier, status, intrinsics, inputs, records
):
    shot = _club_shot(path_tier, path_tier, status, intrinsics)

    assert _inputs(shot) == {"club_path": inputs, "attack_angle": inputs}
    assert _statuses(shot, "club_path", "attack_angle") == records
    # the spin axis is built on them: never stronger than its weakest input
    axis = _statuses(shot, "spin_axis")[0]
    assert source_chain.weakest_label(axis, records[0]) == axis


def test_a_nominal_lens_says_why_in_the_club_record():
    metrics = _club_shot(intrinsics="nominal").to_dict()["metrics"]

    assert labels.NOMINAL_LENS_NOTE in metrics["club_path"]["note"]


def test_an_experimental_club_input_makes_the_simulator_refuse_the_axis():
    from openflight.sim.resolver import resolve_shot  # noqa: PLC0415
    from openflight.sim.types import PlayerState, UnmeasuredShotError  # noqa: PLC0415

    shot = _club_shot("experimental", "experimental")
    assert labels.experimental_collision_inputs(shot.collision_model) == [
        "club_path",
        "attack_angle",
    ]
    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(shot, PlayerState())
    assert "spin axis (experimental)" in refused.value.missing


def test_an_older_log_s_camera_input_without_the_flag_still_reads_experimental():
    record = {
        "inputs": {"club_path": {"value": 1.0, "label": "estimated", "source": "camera_head_track"}}
    }
    assert labels.experimental_collision_inputs(record) == ["club_path"]
    record = {
        "inputs": {
            "club_path": {
                "value": 1.0,
                "label": "estimated",
                "source": "camera_head_track",
                "experimental": False,
            }
        }
    }
    assert labels.experimental_collision_inputs(record) == []


# ---------------------------------------------------------------- 6: the lens's calibration

NOMINAL_LENS = {"lens_id": "6mm", "intrinsics": "nominal", "focal_px_by_arm": {"arm5": 2140.0}}
CALIBRATED_LENS = {**NOMINAL_LENS, "intrinsics": "calibrated", "focal_px_by_arm": {"arm5": 2087.5}}


def test_mode_focal_takes_the_calibrated_focal_and_its_sigma():
    from openflight.camera import resting_ball_range  # noqa: PLC0415

    assert chain.mode_focal(1280, lens=NOMINAL_LENS, rig=None) == (
        2140.0,
        "lens_nominal",
        resting_ball_range.NOMINAL_FOCAL_REL_SD,
    )
    assert chain.mode_focal(1280, lens=CALIBRATED_LENS, rig=None) == (
        2087.5,
        chain.CALIBRATED_FOCAL_SOURCE,
        resting_ball_range.CALIBRATED_FOCAL_REL_SD,
    )
    # a calibration that states its own focal sigma is taken at its word
    stated = {**CALIBRATED_LENS, "focal_rel_sd": 0.012}
    assert chain.mode_focal(1280, lens=stated, rig=None)[2] == 0.012


def _size(focal_source):
    return {
        "status": "measured_from_size",
        "slant_range_m": 2.08,
        "uncertainty_m": 0.05,
        "focal_source": focal_source,
    }


@pytest.mark.parametrize(
    ("focal_source", "label"),
    [("unit_calibration", "estimated"), ("lens_nominal", "experimental"), (None, "experimental")],
)
def test_the_resting_ball_size_range_follows_the_lens(focal_source, label):
    resolution = chain.ball_range_resolution(
        shot_range={"ball_range_reason": "no IWR capture matched this shot"},
        size_route=lambda: _size(focal_source),
    )

    assert (resolution.source, resolution.label) == ("camera_ball_size", label)


def test_the_size_range_is_measured_only_after_a_tape_check_a_future_hook():
    assert chain.size_range_label("unit_calibration") == "estimated"
    assert chain.size_range_label("unit_calibration", tape_checked=True) == "measured"
    assert chain.size_range_label("lens_nominal", tape_checked=True) == "experimental"


def test_a_calibrated_size_range_makes_what_rests_on_it_estimated():
    shot = _shot(
        ball_range_m=2.08,
        ball_range_source="camera_ball_size",
        ball_range_label="estimated",
        launch_angle_vertical=15.4,
        launch_angle_vertical_source="camera_fallback",
        camera_lens_intrinsics="calibrated",
    )

    metrics = shot.to_dict()["metrics"]

    assert metrics["ball_range"]["status"] == "estimated"
    assert "calibrated lens" in metrics["ball_range"]["note"]
    assert metrics["launch_vertical"]["status"] == "estimated"


def test_the_server_hands_the_size_route_the_calibrated_focal(monkeypatch):
    captured = {}

    def fake_route(archive, **kwargs):
        captured.update(kwargs)
        return lambda: "not run"

    monkeypatch.setattr(chain, "size_range_route", fake_route)
    monkeypatch.setattr(server_module, "rig_geometry_config", {"lens": CALIBRATED_LENS})
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "setup_handoff_config", {"scene": {}})
    archive = {"frames": __import__("numpy").zeros((2, 800, 1280), dtype="uint8")}

    server_module._resolve_shot_ball_range(_shot(), archive)  # pylint: disable=protected-access

    assert captured["calibrated_focal_px"] == 2087.5
    assert captured["lens_focal_px"] is None


@pytest.mark.parametrize(
    ("lens", "stamp"), [(CALIBRATED_LENS, "calibrated"), (NOMINAL_LENS, "nominal"), (None, None)]
)
def test_each_camera_shot_is_stamped_with_its_lens_s_intrinsics(monkeypatch, lens, stamp):
    monkeypatch.setattr(server_module, "rig_geometry_config", {"lens": lens})
    monkeypatch.setattr(server_module, "camera_capture_config", {})

    assert server_module._camera_lens_intrinsics() == stamp  # pylint: disable=protected-access


def test_a_collision_spin_explains_the_ops_reading_it_displaced():
    shot = _club_shot(intrinsics="calibrated")
    shot.spin_rpm_measured = 10986.0
    shot.spin_method_measured = "multitaper_ungated"
    shot.spin_rpm = 6200.0
    shot.spin_source = "calculated"
    shot.spin_confidence = None
    shot.spin_method = "collision model"

    server_module._resolve_spin(shot)

    spin = shot.input_sources["spin"]
    assert spin["route"] == "collision"
    assert (
        spin["tried"][0]["reason"]
        == "the OPS spin reading was not accepted; collision spin replaced it"
    )


def test_every_collision_output_notes_the_centre_strike_assumption():
    shot = _club_shot(intrinsics="calibrated")
    shot.spin_rpm = 6200.0
    shot.spin_rpm_collision = 6200.0
    shot.spin_source = "calculated"
    metrics = shot.to_dict()["metrics"]
    for name in (
        "face_angle",
        "dynamic_loft",
        "spin_loft",
        "face_to_path",
        "spin_axis",
        "spin_rate",
    ):
        assert metrics[name]["value"] is not None, name
        assert "centre-face strike" in metrics[name]["note"], name


def test_the_simulator_uses_the_metric_record_s_range_label(monkeypatch):
    from openflight.sim import resolver
    from openflight.sim.types import PlayerState, UnmeasuredShotError

    shot = _club_shot(intrinsics="calibrated")
    shot.ball_range_source = "camera_prior"
    shot.ball_range_label = "estimated"
    metrics = shot.to_dict()["metrics"]
    assert metrics["launch_vertical"]["status"] == "estimated"
    with pytest.raises(UnmeasuredShotError):
        resolver.resolve_shot(shot, PlayerState())

    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_ESTIMATED", True)
    resolved = resolver.resolve_shot(shot, PlayerState())
    assert resolved.provenance["vla"] == metrics["launch_vertical"]["status"]
    assert resolved.provenance["hla"] == metrics["launch_horizontal"]["status"]
    assert resolved.provenance["spin_axis"] == metrics["spin_axis"]["status"]


def test_an_experimental_range_blocks_simulators_even_when_estimates_are_enabled(monkeypatch):
    from openflight.sim import resolver
    from openflight.sim.types import PlayerState, UnmeasuredShotError

    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_ESTIMATED", True)
    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_MODEL_GEOMETRY", True)
    shot = _club_shot(intrinsics="calibrated")
    shot.ball_range_source = "camera_ball_size"
    shot.ball_range_label = "experimental"
    state = PlayerState()

    with pytest.raises(UnmeasuredShotError) as refused:
        resolver.resolve_shot(shot, state)
    assert "vertical launch (experimental)" in refused.value.missing
    assert state.shot_counter == 0


def test_an_older_iwr_candidate_without_the_new_flag_is_experimental():
    record = {
        "inputs": {"attack_angle": {"value": -4.0, "label": "estimated", "source": "iwr_candidate"}}
    }
    assert labels.experimental_collision_inputs(record) == ["attack_angle"]


def test_an_older_iwr_candidate_cannot_reach_simulators_as_an_estimate(monkeypatch):
    from openflight.sim import resolver
    from openflight.sim.types import PlayerState, UnmeasuredShotError

    shot = _club_shot(intrinsics="calibrated")
    shot.collision_model["inputs"]["attack_angle"] = {
        "value": -4.0,
        "label": "estimated",
        "source": "iwr_candidate",
    }
    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_ESTIMATED", True)
    assert shot.to_dict()["metrics"]["attack_angle"]["status"] == "experimental"
    with pytest.raises(UnmeasuredShotError):
        resolver.resolve_shot(shot, PlayerState())
