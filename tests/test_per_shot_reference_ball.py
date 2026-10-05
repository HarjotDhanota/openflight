"""Each shot's resting ball comes from that shot's own frames (D17).

The kiosk used to settle a shot's reference ball with the median of up to 15 earlier
shots' balls, carried for the life of the server. No shot's data may shape another's:
a shot whose two detectors disagree, or that finds nothing, withholds its reference
ball and says why.
"""

import numpy as np
import pytest

from openflight.camera import ball_flight, club_delivery, fusion_processing as fp
from openflight.camera.ball_flight import CameraBallGeometry, estimate_camera_ball_flight
from openflight.camera.club_delivery import CameraDeliveryGeometry, estimate_chained_delivery
from openflight.camera.club_motion import ReferenceBall
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.clubs import ClubType


def _ball(x=50.0, y=70.0, diameter=14.0):
    return ReferenceBall(x, y, diameter, round(np.pi * (diameter / 2) ** 2))


def _ball_geometry(size=(100, 100)):
    return CameraBallGeometry(
        camera_height_m=0.095,
        radar_height_m=0.051,
        tee_range_m=1.5,
        ball_height_m=0.021,
        image_width_px=size[0],
        image_height_px=size[1],
    )


def _detectors(monkeypatch, scene, impact):
    def give(found):
        def detector(*_args, **_kwargs):
            if found is None:
                raise ValueError("not found")
            return found

        return detector

    monkeypatch.setattr(ball_flight, "detect_reference_ball", give(scene))
    monkeypatch.setattr(ball_flight, "detect_impact_reference_ball", give(impact))


def test_there_is_no_session_tracker_to_carry_balls_between_shots():
    assert not hasattr(club_delivery, "ReferenceBallTracker")


def test_disagreeing_detectors_withhold_the_ball_rather_than_ask_earlier_shots(monkeypatch):
    _detectors(monkeypatch, scene=_ball(80.0), impact=_ball(52.0))

    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        np.zeros((30, 100, 100), np.uint8), 15, _ball_geometry()
    )

    assert selected is None
    assert diagnostics["selected_source"] == "detector_disagreement"
    assert diagnostics["disagreement"] is True


@pytest.mark.parametrize(
    ("scene", "impact", "source", "x"),
    [
        (_ball(50.0), _ball(52.0), "detector_agreement", 52.0),
        (_ball(50.0), None, "scene_only", 50.0),
        (None, _ball(52.0), "impact_only", 52.0),
    ],
)
def test_a_shot_s_reference_ball_is_its_own_detection(monkeypatch, scene, impact, source, x):
    _detectors(monkeypatch, scene=scene, impact=impact)

    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        np.zeros((30, 100, 100), np.uint8), 15, _ball_geometry()
    )

    assert selected.x == pytest.approx(x)
    assert diagnostics["selected_source"] == source


def test_a_shot_that_finds_no_ball_says_so_whatever_earlier_shots_found(monkeypatch):
    frames = np.zeros((20, 200, 320), dtype=np.uint8)
    timestamps = np.arange(20, dtype=np.int64) * 2_000_000
    geometry = CameraBallGeometry(
        camera_height_m=0.2032,
        radar_height_m=0.1524,
        tee_range_m=1.524,
        ball_height_m=0.04,
        image_width_px=320,
        image_height_px=200,
    )

    def shot():
        return estimate_camera_ball_flight(
            frames,
            timestamps,
            trigger_ns=10_000_000,
            range_evidence=None,
            geometry=geometry,
            ops_ball_speed_mph=100.0,
        )

    _detectors(monkeypatch, scene=_ball(160.0, 100.0), impact=_ball(160.0, 100.0))
    for _ in range(4):
        shot()
    _detectors(monkeypatch, scene=None, impact=None)

    assert shot().status == "rejected_reference_ball_not_found"


def test_the_club_stage_finds_no_ball_on_its_own_and_borrows_none(monkeypatch):
    monkeypatch.setattr(
        club_delivery,
        "detect_reference_ball",
        lambda _frames, **_kwargs: (_ for _ in ()).throw(ValueError("not found")),
    )

    result = estimate_chained_delivery(
        np.full((60, 200, 320), 200, dtype=np.uint8),
        np.arange(60, dtype=np.int64) * 2_000_000,
        trigger_index=40,
        range_evidence=None,
        geometry=CameraDeliveryGeometry(
            camera_height_m=0.2032,
            radar_height_m=0.1524,
            tee_range_m=1.524,
            ball_height_m=0.04,
            image_width_px=320,
            image_height_px=200,
        ),
        ops_club_speed_mph=80.0,
    )

    assert result.status == "rejected_no_ball"


# ---- the frozen fusion context carries no earlier shots' balls ------------------------


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
        ball_diameter_m=0.04267,
    )


def _context():
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
    )


def _archive():
    return {
        "frames": np.zeros((20, 6, 8), np.uint8),
        "host_timestamp_ns": np.arange(20, dtype=np.int64),
        "trigger_host_timestamp_ns": np.asarray(10, dtype=np.int64),
        "pre_trigger_count": np.asarray(10, dtype=np.int64),
        "_capture_npz_sha256": "capture-hash",
    }


def test_the_frozen_context_holds_no_tracker_state():
    context = _context()

    assert "ball_tracker" not in context and "club_tracker" not in context
    assert context["version"] == 2


def test_a_fusion_result_hands_no_tracker_state_to_the_next_shot():
    result = fp.process_camera_fusion(_context(), _archive())

    assert "next_ball_tracker" not in result and "next_club_tracker" not in result


def test_a_recorded_version_1_context_replays_without_its_earlier_shots_balls():
    # a kiosk before D17 froze each shot's tracker state from earlier shots; replaying
    # it ignores that state and says so
    legacy = {key: value for key, value in _context().items() if key != "sha256"}
    legacy["version"] = 1
    earlier = [{"x": 3.0, "y": 4.0, "diameter_px": 12.0, "area_px": 100}] * 3
    for name in ("ball_tracker", "club_tracker"):
        legacy[name] = {
            "schema_version": 1,
            "max_samples": 15,
            "min_fallback_samples": 3,
            "samples": earlier,
        }
    legacy["sha256"] = fp.geometry_fingerprint(legacy)

    result = fp.process_camera_fusion(legacy, _archive())

    assert any("earlier shots" in note for note in result["notes"])
    assert "next_ball_tracker" not in result


def test_the_server_labels_unavailable_contexts_with_the_current_version():
    from openflight import server

    assert server.CAMERA_FUSION_CONTEXT_VERSION == fp.VERSION
