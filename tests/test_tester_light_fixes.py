"""Light-audit fixes for the setup camera step (13 Oct, light-audit.md).

Each test names the audit section it pins.
"""

from __future__ import annotations

import logging

import numpy as np

from openflight.camera import tester_server as ts
from tests.test_tester_server import _guided_camera, _guided_patch, _guided_result


def _locked_controller(monkeypatch, pose, estimate=None):
    """A static exposure controller held locked and passing on a still ball."""
    monkeypatch.setattr(
        ts, "estimate_patch_ball", estimate or (lambda *_args, **_kwargs: _guided_result())
    )
    frozen = {"status": "stable", "camera_pitch_deg": 0.0, "roll_deg": -2.0}
    controller = ts.StaticExposureController(
        lambda: ts.GuidedRangeAnalyzer(
            _guided_camera(), frozen, lambda: dict(pose), _guided_patch()
        ),
        max_exposure_us=2000,
        change_controls=lambda exposure, gain, owner=None: None,
        black_floor_dn=16.0,
    )
    frames = np.full((5, 200, 320), 70, dtype=np.uint8)
    for observation_id in range(1, 40):
        step = controller._active_step()  # pylint: disable=protected-access
        controller.observe(
            frames,
            observation_id,
            observed_at=float(observation_id),
            applied_controls=[(float(step.exposure_us), float(step.gain))] * 5,
        )
        if controller.status()["locked_and_passing"]:
            return controller, frames, observation_id
    raise AssertionError(f"the controller never locked: {controller.status()}")


class TestSaveFreezesTheExposureStatusAtPress:
    """§1 'Why step 6 failed at 1.32 m': the controller read its status after the search."""

    def test_a_transient_pose_look_during_the_save_search_does_not_fail_the_save(self, monkeypatch):
        pose = {"status": "stable", "camera_pitch_deg": 0.0, "roll_deg": -2.0}
        holder = {}
        state = {"saving": False}

        def estimate(*_args, **kwargs):
            if state["saving"] and kwargs.get("search") is not None and "roll_deg" in pose:
                # the live loop's look lands during the 18 s whole-patch search, while
                # the LIS3DH blips past tolerance; the pose is back before Save ends
                state["saving"] = False
                pose["roll_deg"] = -2.9
                controller = holder["controller"]
                step = controller._active_step()  # pylint: disable=protected-access
                controller.observe(
                    holder["frames"],
                    holder["next_id"],
                    observed_at=float(holder["next_id"]),
                    applied_controls=[(float(step.exposure_us), float(step.gain))] * 5,
                )
                pose["roll_deg"] = -2.0
            return _guided_result()

        controller, frames, last_id = _locked_controller(monkeypatch, pose, estimate)
        holder.update(controller=controller, frames=frames, next_id=last_id + 1)
        step = controller._active_step()  # pylint: disable=protected-access
        state["saving"] = True

        _result, analysis, reason = controller.analyze_for_save(
            frames, last_id, [(float(step.exposure_us), float(step.gain))] * 5
        )

        assert state["saving"] is False, "the live look never landed during the search"
        assert reason is None
        assert analysis["static_exposure"]["locked_and_passing"] is True
        assert analysis["static_exposure"]["frozen_at"] == "save_pressed"

    def test_a_save_pressed_without_a_passing_lock_is_still_refused(self, monkeypatch):
        pose = {"status": "stable", "camera_pitch_deg": 0.0, "roll_deg": -2.0}
        controller, frames, last_id = _locked_controller(monkeypatch, pose)
        step = controller._active_step()  # pylint: disable=protected-access
        pose["roll_deg"] = -2.9
        controller.observe(
            frames,
            last_id + 1,
            observed_at=float(last_id + 1),
            applied_controls=[(float(step.exposure_us), float(step.gain))] * 5,
        )
        pose["roll_deg"] = -2.0

        _result, _analysis, reason = controller.analyze_for_save(
            frames, last_id + 1, [(float(step.exposure_us), float(step.gain))] * 5
        )

        assert reason is not None


class TestPoseChangesAreLogged:
    """§1: 'Pose changes are not logged ... how often this happens is unknown'."""

    def test_every_pose_change_is_logged_with_its_pitch_and_roll_deltas(self, caplog):
        pose = {"status": "stable", "camera_pitch_deg": 0.0, "roll_deg": -2.0}
        analyzer = ts.GuidedRangeAnalyzer(
            _guided_camera(),
            {"status": "stable", "camera_pitch_deg": 0.0, "roll_deg": -2.0},
            lambda: dict(pose),
            _guided_patch(),
        )
        caplog.set_level(logging.INFO, logger=ts.logger.name)

        pose["camera_pitch_deg"] = 0.7
        assert analyzer._orientation_problem() is not None  # pylint: disable=protected-access
        pose["camera_pitch_deg"] = 0.0
        assert analyzer._orientation_problem() is None  # pylint: disable=protected-access
        pose["roll_deg"] = -2.8
        assert analyzer._orientation_problem() is not None  # pylint: disable=protected-access

        changes = [record.getMessage() for record in caplog.records if "pose" in record.message]
        assert len([message for message in changes if "changed" in message]) == 2
        assert any("pitch +0.70" in message for message in changes)
        assert any("roll -0.80" in message for message in changes)
        assert any("returned" in message for message in changes)
