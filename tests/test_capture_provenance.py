"""What a capture records about itself: the resolved mode and a ball that clips."""

from __future__ import annotations

import numpy as np

from openflight.camera import auto_exposure
from openflight.camera.capture_runtime import resolved_camera_config


class _Camera:
    def __init__(self, config):
        self._config = config

    def camera_configuration(self):
        return self._config


class TestResolvedCameraConfig:
    def test_it_keeps_what_decides_the_readout_and_nothing_else(self):
        camera = _Camera(
            {
                "main": {"size": (640, 400), "format": "YUV420", "stride": 640},
                "raw": {"size": (640, 400), "format": "R8"},
                "sensor": {"output_size": (640, 400), "bit_depth": 8},
                "controls": {
                    "ExposureTime": 87,
                    "AnalogueGain": 6.0,
                    "FrameDurationLimits": (8333, 8333),
                    "AeEnable": False,
                    "ScalerCrop": (0, 0, 1280, 800),
                },
                "buffer_count": 8,
            }
        )
        resolved = resolved_camera_config(camera)
        assert resolved["raw"] == {"size": [640, 400], "format": "R8"}
        assert resolved["sensor"] == {"output_size": [640, 400], "bit_depth": 8}
        assert resolved["controls"] == {
            "ExposureTime": 87,
            "AnalogueGain": 6.0,
            "FrameDurationLimits": [8333, 8333],
            "ScalerCrop": [0, 0, 1280, 800],
        }
        assert "AeEnable" not in resolved["controls"]
        assert "buffer_count" not in resolved

    def test_a_camera_that_cannot_report_yields_none_not_an_exception(self):
        class Broken:
            def camera_configuration(self):
                raise RuntimeError("not started")

        assert resolved_camera_config(Broken()) is None

    def test_an_empty_configuration_is_recorded_as_empty(self):
        resolved = resolved_camera_config(_Camera({}))
        assert resolved["raw"] == {"size": [], "format": None}


def _frame(median: int, *, clipped_px: int = 0) -> np.ndarray:
    """A 320x200 frame at a flat level with a small saturated patch in the hitting zone."""
    frame = np.full((200, 320), median, dtype=np.uint8)
    # a little texture so contrast clears the too-dark gate
    frame[::7, :] = min(255, median + 60)
    frame[:, ::11] = max(0, median - 40)
    if clipped_px:
        side = int(np.ceil(np.sqrt(clipped_px)))
        frame[140 : 140 + side, 160 : 160 + side] = 255
    return frame


class TestABallSizedHighlightIsNotGood:
    def test_a_clean_scene_is_good(self):
        observation = auto_exposure.measure_exposure(_frame(110))
        assert observation.status == "good"

    def test_a_saturated_ball_makes_the_scene_marginal_and_asks_for_darker(self):
        # ~0.4 % of the frame, well under the old 2 % gate that let it through
        observation = auto_exposure.measure_exposure(_frame(110, clipped_px=110))
        assert observation.status == "marginal"
        assert observation.recommendation == "darker"
        assert "ball-sized highlight" in observation.message

    def test_a_specular_speck_is_still_tolerated(self):
        # a handful of pixels is a highlight on a glossy ball, not a lost edge
        observation = auto_exposure.measure_exposure(_frame(110, clipped_px=4))
        assert observation.status == "good"

    def test_the_gate_matches_the_gain_screen(self):
        from openflight.camera import tester_server

        results = [{"gain": 4.0, "mean": 120.0, "clipped_pct": auto_exposure.BALL_CLIP_PCT + 0.05}]
        assert tester_server.choose_gain(results)["lighting_required"] is True


class TestBallSpeedContract:
    def test_the_shot_says_which_speed_it_carries(self):
        from datetime import datetime

        from openflight.launch_monitor import Shot

        shot = Shot(ball_speed_mph=110.0, timestamp=datetime.now())
        assert shot.ball_speed_contract is None
        shot.ball_speed_contract = "radial"
        assert shot.to_dict()["ball_speed_contract"] == "radial"
