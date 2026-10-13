"""harjot-indoor-test-2 through the whole fixed-box chain, on its own frame and radar.

30 Sept 2026: the ball was taped about 1.2 m from the radar and a door stands at
1.575 m, a still reflector 6x anything else in the empty scene. The camera found
the ball (34 px); the radar's coherent difference put the door first and the ball
4th, and a Save race (fixed by ebecddb6) lost the setup. Here the saved Save frame
and both radar captures go through the box, the camera's search in it, the radar's
candidates in the box's window, the pairing and the hand-off to swings: the ball's
1.20 m is chosen and the door never is.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from openflight import tee_range
from openflight.camera import (
    ground_patch,
    lens_profiles,
    patch_pairing,
    placement_box,
    tester_server as ts,
)
from openflight.rig_geometry import RigGeometry

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "indoor_test_2"
V3_RIG = REPO / "config" / "enclosure_v3_rig_geometry.json"
CALIBRATION = REPO / "config" / "iwr6843_calibration_reference.json"
DOOR_M = 1.575


@pytest.fixture(scope="module", name="setup")
def _setup():
    return json.loads((FIXTURE / "setup.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module", name="frames")
def _frames(setup):
    with np.load(FIXTURE / setup["frame"]["file"]) as data:
        frame = data["frame"]
    return np.repeat(frame[None], 3, axis=0)


@pytest.fixture(scope="module", name="chain")
def _chain(setup, frames):  # pylint: disable=too-many-locals
    """Everything the setup computes, in the order it computes it."""
    orientation = setup["orientation_at_start"]
    lens = lens_profiles.active_lens(
        RigGeometry.from_json(V3_RIG), lens_profiles.load_catalogue(), "stock-2.8mm"
    )
    camera = ts._reference_ball_camera(  # noqa: SLF001
        ts.ARMS["arm5"], V3_RIG, orientation, None, None, None, lens
    )
    roll = ts._placement_roll_deg(V3_RIG, orientation)  # noqa: SLF001
    record = ts.fixed_box_record(camera, orientation, roll, lens=lens)
    search = ground_patch.PatchSearch.from_record(record)
    result, analysis = ts._guided_camera_analysis(  # noqa: SLF001
        frames, camera, search, analysis_role="independent_save_confirmation"
    )
    ball = patch_pairing.camera_ball(result.selected, camera)
    window = ts.patch_radar_window(record)
    radar = ts._guided_iwr_candidate(  # noqa: SLF001
        setup["empty"],
        setup["present"],
        epoch_id=setup["epoch_id"],
        calibration_path=CALIBRATION,
        qualification=None,
        rig_geometry=V3_RIG,
        patch_window=window,
    )
    ray = placement_box.ray_window(
        camera,
        (ball["x_px"], ball["y_px"]),
        pad_deg=placement_box.load_config().uncalibrated_window_pad_deg,
        far_cap_m=ground_patch.FAR_CAP_M,
    )
    camera_candidate = {
        "evidence": {
            "result": {"status": result.status},
            "patch_ball_camera": ball,
        }
    }
    decision = ts.patch_ball_decision(
        camera_candidate,
        radar.to_dict(),
        camera_status="found",
        camera_ray={"ray_window_m": ray["window_m"]},
        box_range_m=[
            record["nominal_edges_m"]["near_slant_m"] - 0.1,
            record["nominal_edges_m"]["far_slant_m"] + 0.1,
        ],
    )
    return {
        "camera": camera,
        "record": record,
        "search": search,
        "result": result,
        "analysis": analysis,
        "ball": ball,
        "radar": radar,
        "ray": ray,
        "decision": decision,
        "lens": lens,
    }


def test_the_ball_lies_inside_the_fixed_box_drawn_at_that_day_s_tilt(chain):
    """Taped 1.2 m out, short of the 1.8 m window: inside the box only because the
    uncalibrated tilt pads it, and labelled closer than the window, never blocked."""
    ball = chain["ball"]
    assert ground_patch.point_in_polygon(ball["x_px"], ball["y_px"], chain["search"].outline_px)
    assert chain["record"]["mode"] == "fixed"
    assert placement_box.distance_note(chain["camera"], 1.2) == (
        "closer than 1.8 m: numbers labelled, not blocked"
    )


def test_the_camera_finds_the_ball_in_the_box(chain):
    assert chain["result"].status == "selected"
    ball = chain["ball"]
    # where the field camera found it: (531, 531), 34 px across, about 1.2 m by size.
    # The lit-sphere fit lands between 32 and 44 px depending on its size hypotheses;
    # the size is only a sanity check (its 21 % sigma covers either)
    assert ball["x_px"] == pytest.approx(531.0, abs=5.0)
    assert ball["y_px"] == pytest.approx(531.0, abs=5.0)
    assert 30.0 <= ball["diameter_px"] <= 46.0
    assert ball["range_m"] == pytest.approx(1.2, abs=0.3)
    state = placement_box.classify_ball(
        chain["analysis"], None, outline_px=chain["search"].outline_px, settled=True
    )
    assert state["state"] == "found"


def test_the_radar_reports_the_ball_and_flags_the_door(chain):
    found = chain["radar"].evidence["patch_candidates"]
    coherent = [item for item in found["candidates"] if item["method"] == "coherent"]
    magnitude = [item for item in found["candidates"] if item["method"] == "magnitude"]

    assert any(abs(item["range_m"] - 1.20) < 0.03 for item in coherent)
    assert any(abs(item["range_m"] - 1.29) < 0.03 for item in magnitude)
    # the door: a dominant still reflector, flagged; no coherent peak stands on it
    assert any(abs(item["range_m"] - DOOR_M) < 0.03 for item in found["still_reflectors"])
    for item in found["candidates"]:
        if abs(item["range_m"] - DOOR_M) < 0.03:
            assert item["still_reflector"] is not None


def test_the_pair_is_the_ball_at_1_20_m_never_the_door(chain):
    decision = chain["decision"]

    assert decision["status"] == "validated"
    assert decision["source"] == "static_iwr_coherent_magnitude"
    assert decision["range_m"] == pytest.approx(1.20, abs=0.02)
    assert decision["camera_distance"] == "size_sanity_check"
    for pair in decision["radar_pairs"]:
        assert abs(pair["range_m"] - DOOR_M) > 0.05
    # the ball sat about 0.14 m left of the radar's axis
    assert decision["side_offset_m"] == pytest.approx(-0.14, abs=0.04)


def test_the_swings_are_started_with_the_ball_s_range_and_side_offset(chain, setup):
    solution = ts.patch_ball_solution(
        setup["epoch_id"], [chain["radar"]], chain["decision"], chain["record"]
    )

    args, handed = ts.tee_range_handoff(solution, rig_geometry=V3_RIG, lens=chain["lens"])

    assert solution.reason == ts.PATCH_BALL_SAVED_REASON
    assert float(args[args.index("--iwr6843-tee-m") + 1]) == pytest.approx(1.20, abs=0.02)
    assert args[args.index("--iwr6843-tee-range-source") + 1] == (
        "patch_validated_static_iwr_coherent_magnitude"
    )
    assert float(args[args.index("--tee-side-offset-m") + 1]) == pytest.approx(-0.14, abs=0.04)
    assert args[args.index("--camera-lens-id") + 1] == "stock-2.8mm"
    assert handed["experimental"] is True
    assert handed["patch_ball"]["status"] == "validated"
    assert tee_range.TeeRangeSolution.from_dict(solution.to_dict()).reason == solution.reason


def test_a_camera_without_the_ball_never_takes_the_door(chain):
    """The no-ball outcome: the radar's candidates are on record, never the range."""
    found = chain["radar"].evidence["patch_candidates"]["candidates"]

    missed = patch_pairing.pair_patch_ball(None, found, camera_status="no_ball")
    blind = patch_pairing.pair_patch_ball(
        None,
        found,
        camera_status="unavailable",
        coherent_available=True,
        box_range_m=[1.7, 3.0],
    )

    assert missed["status"] == "no_ball" and missed["range_m"] is None
    # a camera that could not look: no agreed radar pair inside the box (the ball was
    # short of it, the door is a still reflector), so nothing is saved
    assert blind["range_m"] is None
