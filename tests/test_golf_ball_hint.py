"""Experimental model evidence cannot replace geometry, rivals or current pixels."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import golf_ball_hint as hints, tester_server as ts
from tests.camera_helpers import v3_camera
from tests.test_image_box_ball import image_search
from tests.test_tester_box_contract import _locator


def test_class_zero_decode_letterbox_and_nms_keep_separate_rivals():
    class Net:
        def setInput(self, blob):  # pylint: disable=invalid-name
            self.blob = blob

        def forward(self):
            return np.array(
                [
                    [
                        [160, 160, 80, 60],
                        [160] * 4,
                        [32, 32, 32, -1],
                        [32] * 4,
                        [0.9, 0.8, 0.85, 0.99],
                    ]
                ],
                np.float32,
            )

    detector = hints.GolfDetector.__new__(hints.GolfDetector)
    detector.net = Net()
    found = detector.detect(np.full((100, 200), 128, np.uint8))
    assert len(found) == 2
    assert found[0]["center_px"] == pytest.approx([100, 50])
    assert found[1]["center_px"] == pytest.approx([50, 50])
    assert detector.net.blob.shape == (1, 3, 320, 320)
    assert 0 <= detector.net.blob.min() <= detector.net.blob.max() <= 1


class Objects:
    def __init__(self, centers, score=0.9):
        self.centers = centers
        self.score = score
        self.calls = 0

    def detect(self, _image):
        # The first seed's two crops start at x=40/104, both at y=72.
        origin = (40, 104)[self.calls % 2]
        self.calls += 1
        return [
            {
                "center_px": [x - origin, y - 72],
                "box_px": [x - origin - 12, y - 72 - 12, 24, 24],
                "score": self.score,
            }
            for x, y in self.centers
        ]


def test_two_crops_only_propose_a_location_without_diameter_or_ready_state():
    found = hints.context_hint(
        np.zeros((400, 640), np.uint8), [(10, 200, 200, 12)], Objects([(200, 200)])
    )
    assert found["status"] == "proposed"
    assert found["selected"]["x_px"] == 200
    assert found["selected"]["context_count"] == 2
    assert "diameter_px" not in found["selected"]
    assert not found["confirmable"] and not found["metric_eligible"]


def test_duplicate_edge_crop_is_not_a_second_context():
    detector = Objects([(200, 200)])
    found = hints.context_hint(np.zeros((256, 256), np.uint8), [(10, 128, 128, 12)], detector)
    assert found["selected"] is None
    assert detector.calls == 1


@pytest.mark.parametrize("score", [0.3, 0.59])
def test_weak_contexts_do_not_qualify(score):
    found = hints.context_hint(
        np.zeros((400, 640), np.uint8), [(10, 200, 200, 12)], Objects([(200, 200)], score)
    )
    assert found["selected"] is None


def test_distinct_objects_remain_ambiguous():
    class Rivals:
        calls = 0

        def detect(self, _image):
            origin = [40, 104, 240, 304][self.calls]
            self.calls += 1
            x = 200 if self.calls <= 2 else 400
            return [
                {
                    "center_px": [x - origin, 128],
                    "box_px": [x - origin - 12, 116, 24, 24],
                    "score": 0.9,
                }
            ]

    found = hints.context_hint(
        np.zeros((400, 640), np.uint8), [(10, 200, 200, 12), (9, 400, 200, 12)], Rivals()
    )
    assert found["status"] == "ambiguous"
    assert len(found["context_checked"]) == 2
    assert found["selected"] is None


@pytest.mark.parametrize(
    "applied",
    [
        None,
        [],
        [(300, 4)] * 2,
        [(300, 4), (301, 4), (300, 4)],
        [(float("nan"), 4)] * 3,
        [(300, 0)] * 3,
    ],
)
def test_unknown_or_changed_controls_never_load_model(monkeypatch, applied):
    monkeypatch.setattr(hints, "detector_for", lambda _path: pytest.fail("model must not load"))
    found = hints.propose(
        np.zeros((3, 400, 640), np.uint8),
        v3_camera(640, 400),
        image_search(640, 400),
        applied,
        "unused",
    )
    assert found["selected"] is None
    assert "controls" in found["reason"]


def test_wrong_mode_never_loads_model(monkeypatch):
    monkeypatch.setattr(hints, "detector_for", lambda _path: pytest.fail("model must not load"))
    found = hints.propose(
        np.zeros((3, 400, 640), np.uint8),
        v3_camera(),
        image_search(640, 400),
        [(300, 4)] * 3,
        "unused",
    )
    assert found["reason"] == "frame mode mismatch"


def test_wrong_weights_are_refused(tmp_path):
    path = tmp_path / "wrong.onnx"
    path.write_bytes(b"wrong model")
    with pytest.raises(ValueError, match="pinned"):
        hints.GolfDetector(str(path))


@pytest.mark.parametrize("status", ["unavailable", "ambiguous", "proposed", "error"])
def test_every_hint_outcome_preserves_full_geometry(monkeypatch, status):
    canonical = ts.ReferenceBallRangeResult("ambiguous", "withheld", None, (), {"existing": True})
    calls = []

    def estimate(*_args, **kwargs):
        calls.append(kwargs)
        return canonical

    def propose(*_args):
        if status == "error":
            raise OSError("bad model")
        return {**hints.unavailable("test"), "status": status}

    monkeypatch.setattr(hints, "propose", propose)
    monkeypatch.setattr(hints.ranges, "estimate_patch_ball", estimate)
    kwargs = {
        "search": image_search(640, 400),
        "height_independent": True,
        "ball_center_height_m": 0.02135,
    }
    found = hints.estimate_with_hint(
        np.zeros((3, 400, 640), np.uint8), v3_camera(640, 400), kwargs, "unused", [(300, 4)] * 3
    )
    assert replace(found, diagnostics=canonical.diagnostics) == canonical
    assert calls == [kwargs]
    assert found.diagnostics["object_hint"]["status"] == (
        "unavailable" if status == "error" else status
    )


def test_disabled_worker_keeps_inference_out_of_server_process(monkeypatch):
    canonical = ts.ReferenceBallRangeResult("not_found", "withheld", None, (), {})
    monkeypatch.setattr(ts, "_BALL_SEARCH_POOL", None)
    monkeypatch.setattr(ts, "estimate_patch_ball", lambda *_a, **_k: canonical)
    monkeypatch.setattr(
        hints, "estimate_with_hint", lambda *_a: pytest.fail("no in-process inference")
    )
    found = ts._run_ball_search(
        np.zeros((3, 400, 640), np.uint8), v3_camera(640, 400), {}, object_hint_model=Path("unused")
    )
    assert found.status == canonical.status
    assert found.diagnostics["object_hint"]["status"] == "unavailable"


def test_hint_has_frame_controls_and_mode_identity_but_cannot_confirm(monkeypatch):
    locator, _live, _record = _locator(monkeypatch, [])
    now = [100.0]
    locator._clock = lambda: now[0]
    locator._object_hint_model = Path("unused")
    hint = {
        **hints.unavailable("test"),
        "status": "proposed",
        "selected": {"x_px": 640, "y_px": 440},
    }

    def analyze(*_args, **kwargs):
        assert kwargs["object_hint_model"] == Path("unused")
        return None, {"status": "not_found", "selected": None, "diagnostics": {"object_hint": hint}}

    monkeypatch.setattr(ts, "_guided_camera_analysis", analyze)
    locator.wide = None
    frames = np.full((3, 800, 1280), 105, np.uint8)
    applied = [(300, 4)] * 3
    output = locator(frames, 42, applied, observed_at=99.5)
    observed = output["ball_observation"]["object_hint"]
    assert observed["input_identity"]["frame_sequence"] == 42
    assert observed["input_identity"]["applied_controls"] == applied
    assert observed["input_identity"]["frame_size_px"] == [1280, 800]
    assert not locator.ball()["confirmable"]
    assert locator.ball()["center_px"] is None
    assert locator.ball()["position_estimate"] is None
    stale = locator(frames, 43, applied, observed_at=96)
    assert stale["ball_observation"]["object_hint"]["status"] == "stale"
    assert stale["ball_observation"]["object_hint"]["selected"] is None
    repeated = locator(frames, 42, applied)
    assert repeated == stale


def test_independent_save_never_requests_model_hints(monkeypatch):
    calls = []

    def search(*_args, **kwargs):
        calls.append(kwargs)
        return ts.ReferenceBallRangeResult("not_found", "withheld", None, (), {})

    monkeypatch.setattr(ts, "_run_ball_search", search)
    ts._guided_camera_analysis(
        np.zeros((3, 400, 640), np.uint8),
        v3_camera(640, 400),
        image_search(640, 400),
        analysis_role="independent_save_confirmation",
        object_hint_model=Path("unused"),
    )
    assert calls == [{}]


def test_worker_dispatches_pinned_hint_task(monkeypatch):
    calls = []
    canonical = ts.ReferenceBallRangeResult("not_found", "withheld", None, (), {})

    class Pool:
        def submit(self, func, *args):
            calls.append((func, args))

            class Done:
                def result(self):
                    return canonical

            return Done()

    monkeypatch.setattr(ts, "_BALL_SEARCH_POOL", Pool())
    frames = np.zeros((3, 400, 640), np.uint8)
    controls = [(300, 4)] * 3
    ts._run_ball_search(
        frames,
        v3_camera(640, 400),
        {},
        object_hint_model=Path("model.onnx"),
        applied_controls=controls,
    )
    assert calls[0][0] is hints.estimate_with_hint
    assert calls[0][1][-2:] == ("model.onnx", controls)


def test_spawn_worker_missing_model_still_returns_original_geometry(tmp_path):
    from tests import test_patch_ball_search_field as field

    camera = field.v3(pitch_deg=1.72)
    frames = field.indoor_scene()
    ball = field.INDOOR_BALL_PX
    patch = field.patch_from_centre_pixel(camera, (ball[0], ball[1] + 10))
    kwargs = {
        "search": field.search_for(camera, patch),
        "ball_center_height_m": field.BALL_CENTER_HEIGHT_M,
        "height_independent": True,
    }
    local = ts._run_ball_search(frames, camera, kwargs)
    ts.configure_ball_search_workers(1)
    try:
        remote = ts._run_ball_search(
            frames,
            camera,
            kwargs,
            object_hint_model=tmp_path / "absent.onnx",
            applied_controls=[(300, 4)] * len(frames),
        )
    finally:
        ts.configure_ball_search_workers(0)
    assert remote.status == local.status
    assert remote.selected == local.selected
    assert remote.candidates == local.candidates
    assert remote.diagnostics["object_hint"]["status"] == "unavailable"


@pytest.mark.parametrize("change", ["controls", "mode", "analyzer"])
def test_superseded_live_context_cannot_publish_model_hint(change):
    live = ts.LiveView()
    arm = ts.ARMS["arm5"]
    live._arm = arm
    live._run_generation = 1
    live._frame_sequence = 5
    live._recent.extend([np.zeros((8, 8), np.uint8)] * 3)
    live._recent_applied.extend([(300, 4)] * 3)

    def analyze(*_args):
        if change == "controls":
            live._context_generation += 1
        elif change == "mode":
            live._arm = ts.ARMS["arm6"]
        else:
            live._analyzer = None
        return {"ball_observation": {"object_hint": {"status": "proposed"}}}

    class OneLook:
        calls = 0

        def wait(self, _duration):
            self.calls += 1
            return self.calls > 1

        def is_set(self):
            return False

    live._analyzer = analyze
    live._look(arm, OneLook(), 1)
    assert live._association is None
