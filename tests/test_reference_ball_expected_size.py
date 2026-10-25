"""Two detectors that see one resting ball at different sizes: the size the ball must
have at its distance decides between them, rather than withholding the shot.

Harjot-outdoor-test-1, shot 14 (dusk, 6 mm lens, 145 us x 12): the scene detector
measured the ball 40.6 px across; the impact detector, thresholding the ball's
departure at a fixed 45 DN, kept only its lit upper half, 23.2 px. Both sat on the
ball 6.6 px apart, and the shot was withheld as a detector disagreement.
"""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import ball_flight, fusion_processing as fp
from openflight.camera.ball_flight import CameraBallGeometry, expected_reference_diameter
from openflight.camera.club_motion import ReferenceBall, detect_impact_reference_ball
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.clubs import ClubType

FIXTURE = Path(__file__).parent / "fixtures" / "ball_detector" / "outdoor-test-1-shot-14-dark.npz"
# the last frame exposed by the trigger (pre_trigger_count_by_exposure 19)
SHOT_14_TRIGGER = 18
# shot 14's own resting-ball look before its swing (ball_at_address)
SHOT_14_LOOK = {"x": 793.3, "y": 418.2, "diameter_px": 38.43, "source": "ball_at_address"}
SIX_MM_FOCAL_PX = 2000.0


def shot_14_frames():
    data = np.load(FIXTURE)
    frames = data["frames"]
    x0, y0 = (int(value) for value in data["origin_px"])
    pad = ((0, 0), (y0, 800 - y0 - frames.shape[1]), (x0, 1280 - x0 - frames.shape[2]))
    return np.pad(frames, pad, mode="edge")


def shot_14_geometry():
    return CameraBallGeometry(
        camera_height_m=0.095,
        radar_height_m=0.0522,
        tee_range_m=1.908,
        ball_height_m=0.02135,
        camera_lateral_offset_m=0.0019,
        camera_forward_offset_m=0.0314,
        image_width_px=1280,
        image_height_px=800,
    )


def test_in_dark_frames_the_impact_detector_measures_only_the_lit_cap():
    frames = shot_14_frames()

    ball = detect_impact_reference_ball(
        frames, trigger_frame_index=SHOT_14_TRIGGER, pixel_scale=2.0, region=(678, 303, 908, 533)
    )

    # no pixel reaches the 210 DN refinement; the 45 DN departure keeps the lit half
    assert ball.x == pytest.approx(793.2, abs=1.0)
    assert ball.diameter_px == pytest.approx(23.2, abs=1.0)


def test_shot_14_takes_the_scene_ball_whose_size_the_ball_s_look_confirms():
    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        shot_14_frames(), SHOT_14_TRIGGER, shot_14_geometry(), setup_ball=SHOT_14_LOOK
    )

    assert selected is not None
    assert selected.x == pytest.approx(797.6, abs=0.5)
    assert selected.diameter_px == pytest.approx(40.6, abs=0.5)
    assert diagnostics["agreement"] is False
    assert diagnostics["selected_source"] == "scene_by_expected_size"
    assert diagnostics["expected_diameter"] == {
        "diameter_px": pytest.approx(38.43),
        "source": "setup_ball",
    }


def test_shot_14_takes_the_scene_ball_whose_size_its_distance_predicts():
    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        shot_14_frames(),
        SHOT_14_TRIGGER,
        shot_14_geometry(),
        setup_ball=SHOT_14_LOOK,
        lens_focal_px=SIX_MM_FOCAL_PX,
    )

    assert selected.diameter_px == pytest.approx(40.6, abs=0.5)
    assert diagnostics["selected_source"] == "scene_by_expected_size"
    expected = diagnostics["expected_diameter"]
    assert expected["source"] == "lens_focal_and_tee_range"
    # 2000 px x 42.7 mm over the lens's 1.88 m to the ball
    assert expected["diameter_px"] == pytest.approx(45.5, abs=0.3)


def test_the_expected_size_prefers_the_unit_s_calibrated_focal_length():
    geometry = shot_14_geometry()
    calibrated = replace(geometry, setup_focal_px=1900.0)

    diameter, source = expected_reference_diameter(
        calibrated, SHOT_14_LOOK, lens_focal_px=SIX_MM_FOCAL_PX
    )

    assert source == "calibrated_focal_and_tee_range"
    assert diameter == pytest.approx(45.5 * 1900.0 / 2000.0, abs=0.3)
    assert expected_reference_diameter(geometry, None, lens_focal_px=None) == (None, None)


def _ball(x, y, diameter):
    return ReferenceBall(x, y, diameter, round(np.pi * (diameter / 2) ** 2))


def _detectors(monkeypatch, scene, impact):
    monkeypatch.setattr(ball_flight, "detect_reference_ball", lambda *_a, **_k: scene)
    monkeypatch.setattr(ball_flight, "detect_impact_reference_ball", lambda *_a, **_k: impact)


def _select(**kwargs):
    return ball_flight._select_reference_ball(  # pylint: disable=protected-access
        np.zeros((30, 800, 1280), np.uint8), 15, shot_14_geometry(), **kwargs
    )


def test_the_impact_ball_wins_when_the_scene_ball_is_the_wrong_size(monkeypatch):
    _detectors(monkeypatch, scene=_ball(800.0, 414.0, 28.0), impact=_ball(802.0, 412.0, 44.0))

    selected, diagnostics = _select(lens_focal_px=SIX_MM_FOCAL_PX)

    assert selected.diameter_px == pytest.approx(44.0)
    assert diagnostics["selected_source"] == "impact_by_expected_size"


@pytest.mark.parametrize(
    ("scene", "impact", "why"),
    [
        (_ball(800.0, 414.0, 28.0), _ball(800.0, 412.0, 20.0), "neither is the size"),
        (_ball(800.0, 414.0, 59.0), _ball(800.0, 412.0, 35.0), "both are the size"),
        (_ball(800.0, 414.0, 44.0), _ball(600.0, 412.0, 22.0), "two different places"),
    ],
)
def test_a_disagreement_the_size_cannot_settle_still_withholds(monkeypatch, scene, impact, why):
    _detectors(monkeypatch, scene=scene, impact=impact)

    selected, diagnostics = _select(lens_focal_px=SIX_MM_FOCAL_PX)

    assert selected is None, why
    assert diagnostics["selected_source"] == "detector_disagreement"


def test_without_a_size_to_compare_a_disagreement_withholds(monkeypatch):
    _detectors(monkeypatch, scene=_ball(800.0, 414.0, 44.0), impact=_ball(800.0, 412.0, 22.0))

    selected, diagnostics = _select()

    assert selected is None
    assert diagnostics["expected_diameter"] == {"diameter_px": None, "source": None}


def _geometry():
    return EffectiveCameraGeometryInputs(
        camera_height_m=0.095,
        radar_height_m=0.051,
        tee_slant_range_m=1.5,
        ball_height_m=0.021,
        camera_lateral_offset_m=0.0,
        camera_forward_offset_m=0.03,
        image_width_px=8,
        image_height_px=6,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        ball_horizontal_output_offset_deg=0.0,
        ball_diameter_m=0.0427,
    )


def _context(**extra):
    return fp.build_context(
        geometry=_geometry(),
        lighting_eligible=True,
        ball_range_evidence=None,
        club_range_evidence=None,
        ops_ball_speed_mph=100.0,
        ops_club_speed_mph=75.0,
        iwr_vertical_deg=18.0,
        iwr_horizontal_deg=1.0,
        iwr_horizontal_confidence=0.8,
        club=ClubType.IRON_7,
        capture_npz_sha256="capture-hash",
        session_uuid="session-a",
        shot_number=3,
        **extra,
    )


def test_the_frozen_context_carries_the_lens_focal_length_only_when_known():
    assert "lens_focal_px" not in _context()
    assert _context(lens_focal_px=2000.0)["lens_focal_px"] == 2000.0


def test_fusion_hands_the_context_s_lens_focal_length_to_the_ball_stage(monkeypatch):
    seen = {}

    def select(*_args, **kwargs):
        seen.update(kwargs)
        return None, {"selected_source": None}

    monkeypatch.setattr(ball_flight, "_select_reference_ball", select)
    archive = {
        "frames": np.zeros((20, 6, 8), np.uint8),
        "host_timestamp_ns": np.arange(20, dtype=np.int64),
        "trigger_host_timestamp_ns": np.asarray(10, dtype=np.int64),
        "pre_trigger_count": np.asarray(10, dtype=np.int64),
        "_capture_npz_sha256": "capture-hash",
    }

    fp.process_camera_fusion(_context(lens_focal_px=2000.0), archive)

    assert seen["lens_focal_px"] == 2000.0


SIX_MM_LENS = {"lens_id": "6mm", "focal_px_by_arm": {"arm5": 2000.0001, "arm6": 1000.0001}}


@pytest.mark.parametrize(
    ("lens", "width", "focal"),
    [
        (SIX_MM_LENS, 1280, 2000.0001),
        (SIX_MM_LENS, 640, 1000.0001),
        (SIX_MM_LENS, 320, None),
        (None, 1280, None),
        ({"focal_px_by_arm": {"arm5": "wide"}}, 1280, None),
    ],
)
def test_the_lens_record_gives_the_focal_length_of_its_own_capture_modes(lens, width, focal):
    assert fp.lens_record_focal_px(lens, width) == focal
