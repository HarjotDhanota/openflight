"""E3 wiring: the full ball speed's two routes, the full club speed and the full smash.

The launch-based projection goes first; the OPS total-speed projection resolves the
shot when it refuses, and the choice with every refusal is kept under
``input_sources["ball_speed_full"]``. The full club speed reads the camera head track's
state from the club delivery; without it it is not measured with the reason.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import openflight.server as server_module
from openflight import shot_metrics, shot_models
from openflight.full_speed import CALCULATED, ESTIMATED, EXPERIMENTAL, NOT_MEASURED
from openflight.rig_geometry import RigGeometry
from tests.test_camera_head_track import Swing
from tests.test_full_speed_routes import _club_track, _head_record
from tests.test_full_speed_server import _shot


@pytest.fixture
def fusion_geometry(monkeypatch):
    rig = RigGeometry.from_json("config/enclosure_v3_rig_geometry.json")
    calibration = SimpleNamespace(
        tee_range_m=2.10,
        tee_ball_height_m=0.02135,
        radar_height_m=rig.enclosure_setup().radar_height_m,
    )
    monkeypatch.setattr(server_module, "rig_geometry", rig)
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace(calibration=calibration))
    monkeypatch.setattr(server_module, "horizontal_offset_deg", None)
    scene = dict(server_module.setup_handoff_config.get("scene") or {})
    scene["tee_side_offset_m"] = 0.03
    monkeypatch.setitem(server_module.setup_handoff_config, "scene", scene)
    monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
    return rig


def _attach(shot):
    shot_models._attach_full_speeds(shot)  # pylint: disable=protected-access
    return shot


def test_the_launch_based_projection_goes_first(fusion_geometry):
    shot = _attach(_shot())

    assert shot.ball_speed_full["status"] == CALCULATED
    assert shot.ball_speed_full["route"] == "measured_projection"
    chain = shot.input_sources["ball_speed_full"]
    assert chain["route"] == "measured_projection" and chain["tried"] == []


def test_the_ops_total_projection_resolves_a_missing_horizontal(fusion_geometry):
    shot = _attach(_shot(launch_angle_horizontal=None, launch_angle_horizontal_source=None))

    full = shot.ball_speed_full
    assert full["route"] == "ops_total_projection"
    assert full["status"] == CALCULATED
    assert full["value_mph"] > shot.ball_speed_mph
    assert shot.ball_speed_full_mph == pytest.approx(full["value_mph"])
    chain = shot.input_sources["ball_speed_full"]
    assert chain["route"] == "ops_total_projection"
    assert chain["label"] == CALCULATED
    assert [entry["route"] for entry in chain["tried"]] == ["measured_projection"]
    assert "horizontal launch" in chain["tried"][0]["reason"]


def test_without_a_side_offset_the_total_projection_is_estimated(fusion_geometry, monkeypatch):
    scene = dict(server_module.setup_handoff_config["scene"])
    scene["tee_side_offset_m"] = None
    monkeypatch.setitem(server_module.setup_handoff_config, "scene", scene)

    full = _attach(_shot()).ball_speed_full

    assert full["route"] == "ops_total_projection"
    assert full["status"] == ESTIMATED
    assert "assumed dead ahead" in full["note"]


def test_no_vertical_launch_leaves_both_routes_refused_with_their_reasons(fusion_geometry):
    shot = _attach(_shot(launch_angle_vertical=None, launch_angle_vertical_source=None))

    full = shot.ball_speed_full
    assert full["status"] == NOT_MEASURED and full["value_mph"] is None
    assert "measured_projection:" in full["reason"] and "ops_total_projection:" in full["reason"]
    assert shot.ball_speed_full_mph is None


def test_the_record_names_the_route_s_source(fusion_geometry):
    shot = _attach(_shot(launch_angle_horizontal=None, launch_angle_horizontal_source=None))

    record = shot_metrics.records(shot.to_dict())["ball_speed_full"]

    assert record["status"] == CALCULATED
    assert "OPS total-speed projection" in record["source"]


def _with_head_track(shot, swing=Swing(), before_contact_ms=6.0):
    shot.club_delivery = {"head_track": _head_record(swing)}
    shot.ops_speed_track = {
        **shot.ops_speed_track,
        **{
            key: value
            for key, value in _club_track(swing, before_contact_ms).items()
            if key == "club_reading"
        },
    }
    shot.ops_speed_track["impact_ms"] = 50.0
    return shot


def test_reading_time_full_club_speed_does_not_supply_full_impact_smash(fusion_geometry):
    shot = _attach(_with_head_track(_shot()))

    club = shot.club_speed_full
    assert club["status"] == EXPERIMENTAL, club["reason"]
    assert club["value_mph"] >= club["reading"]["radial_mph"]
    smash = shot.smash_factor_full
    assert smash["status"] == NOT_MEASURED
    assert smash["value"] is None
    assert "impact" in smash["reason"]
    records = shot_metrics.records(shot.to_dict())
    assert records["club_speed_full"]["status"] == EXPERIMENTAL
    assert records["smash_factor_full"]["status"] == NOT_MEASURED


def test_without_a_head_track_the_full_club_speed_says_why(fusion_geometry):
    shot = _attach(_shot())

    assert shot.club_speed_full["status"] == NOT_MEASURED
    assert "no camera head track" in shot.club_speed_full["reason"]
    assert shot.smash_factor_full["status"] == NOT_MEASURED
    assert "club speed is not a full speed" in shot.smash_factor_full["reason"]


def test_a_failed_horizontal_gate_stays_experimental_in_the_first_route(fusion_geometry):
    shot = _attach(_shot(launch_angle_horizontal_source="radar_low_coherence"))
    assert shot.ball_speed_full["route"] == "measured_projection"
    assert shot.ball_speed_full["status"] == EXPERIMENTAL
    assert shot.to_dict()["metrics"]["ball_speed_full"]["status"] == EXPERIMENTAL


@pytest.mark.parametrize("lens,label", [("nominal", EXPERIMENTAL), ("calibrated", ESTIMATED)])
def test_total_route_uses_the_shots_camera_lens_label(fusion_geometry, lens, label):
    shot = _attach(
        _shot(
            launch_angle_horizontal=None,
            launch_angle_vertical_source="camera_fallback",
            camera_lens_intrinsics=lens,
        )
    )
    assert shot.ball_speed_full["route"] == "ops_total_projection"
    assert shot.ball_speed_full["status"] == label
    assert shot.to_dict()["metrics"]["ball_speed_full"]["status"] == label
    assert shot.ball_speed_full_mph == shot.ball_speed_full["value_mph"]


def test_a_projection_exception_does_not_block_the_other_route(fusion_geometry, monkeypatch):
    def broken(**_kwargs):
        raise ValueError("broken projection")

    monkeypatch.setattr("openflight.full_speed.evaluate_full_ball_speed", broken)
    shot = _attach(_shot())
    assert shot.ball_speed_full["route"] == "ops_total_projection"
    tried = shot.input_sources["ball_speed_full"]["tried"]
    assert "broken projection" in tried[0]["reason"]


def test_total_route_does_not_inherit_an_unused_horizontal(fusion_geometry):
    shot = _shot(launch_angle_horizontal_source="radar_low_coherence")
    shot.ops_speed_track = None
    _attach(shot)
    assert shot.ball_speed_full["route"] == "ops_total_projection"
    assert shot.ball_speed_full["status"] == CALCULATED
    assert shot.to_dict()["metrics"]["launch_horizontal"]["status"] == EXPERIMENTAL
    assert shot.to_dict()["metrics"]["ball_speed_full"]["status"] == CALCULATED
