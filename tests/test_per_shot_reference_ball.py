"""Each shot's resting ball comes from that shot's own frames, or is withheld with a reason."""

import numpy as np
import pytest

from openflight.camera import ball_flight, club_delivery, fusion_processing as fp
from openflight.camera.ball_flight import CameraBallGeometry, estimate_camera_ball_flight
from openflight.camera.club_delivery import CameraDeliveryGeometry, estimate_chained_delivery
from openflight.camera.club_motion import ReferenceBall
from tests.test_camera_fusion_processing import _archive, _context_kwargs


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


COMPACT = dict(
    camera_height_m=0.2032,
    radar_height_m=0.1524,
    tee_range_m=1.524,
    ball_height_m=0.04,
    image_width_px=320,
    image_height_px=200,
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


@pytest.mark.parametrize(
    ("scene", "impact", "source", "x"),
    [
        (_ball(50.0), _ball(52.0), "detector_agreement", 52.0),
        (_ball(50.0), None, "scene_only", 50.0),
        (None, _ball(52.0), "impact_only", 52.0),
        (_ball(80.0), _ball(52.0), "detector_disagreement", None),
    ],
)
def test_a_shot_s_reference_ball_is_its_own_detection(monkeypatch, scene, impact, source, x):
    _detectors(monkeypatch, scene=scene, impact=impact)

    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        np.zeros((30, 100, 100), np.uint8), 15, _ball_geometry()
    )

    assert diagnostics["selected_source"] == source
    if x is None:  # withheld rather than asking earlier shots
        assert selected is None
        assert diagnostics["disagreement"] is True
    else:
        assert selected.x == pytest.approx(x)


def test_a_shot_that_finds_no_ball_says_so_whatever_earlier_shots_found(monkeypatch):
    frames = np.zeros((20, 200, 320), dtype=np.uint8)
    timestamps = np.arange(20, dtype=np.int64) * 2_000_000
    geometry = CameraBallGeometry(**COMPACT)

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
        geometry=CameraDeliveryGeometry(**COMPACT),
        ops_club_speed_mph=80.0,
    )

    assert result.status == "rejected_no_ball"


def _context():
    return fp.build_context(**_context_kwargs())


def test_neither_the_frozen_context_nor_a_result_carries_tracker_state():
    context = _context()
    result = fp.process_camera_fusion(context, _archive())

    assert "ball_tracker" not in context and "club_tracker" not in context
    assert context["version"] == fp.VERSION
    assert "next_ball_tracker" not in result and "next_club_tracker" not in result


def test_a_recorded_version_1_context_replays_without_its_earlier_shots_balls():
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
