"""The tester refuses a placement for another rig at setup, with the kiosk's own checks."""

import json

import pytest

from tests.test_calibrated_projection import _artifact, _placement


class TestTheTesterRunsTheServersPlacementChecks:
    """C11: a placement for another rig is refused at setup, not later at the kiosk."""

    RIG = "config/enclosure_v3_rig_geometry.json"
    TILT = {"pitch_deg": 0.0, "roll_deg": 0.0, "camera_pitch_deg": 0.0}

    def _files(self, tmp_path, **changes):
        from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

        rig = RigGeometry.from_json(self.RIG)
        right_mm, down_mm, forward_mm = rig.iwr_offset_mm
        lens_mm = rig.lens_height_above_floor_mm
        placement = _placement(
            rig_geometry_sha256=rig.snapshot()["sha256"],
            enclosure_to_target_lfu=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            # the v3 file's IWR receive-row offset through the declared mount, read
            # from the file so a re-measured rig keeps this placement consistent
            camera_origin_lfu=[0.0, 0.0, lens_mm / 1000.0],
            radar_origin_lfu=[right_mm / 1000.0, forward_mm / 1000.0, (lens_mm - down_mm) / 1000.0],
            reference_pose_deg={"pitch": 0.0, "roll": 0.0},
        )
        placement.update(changes)
        artifact_path = tmp_path / "optical.json"
        placement_path = tmp_path / "placement.json"
        artifact_path.write_text(json.dumps(_artifact()), encoding="utf-8")
        placement_path.write_text(json.dumps(placement), encoding="utf-8")
        return artifact_path, placement_path

    def _camera(self, artifact_path, placement_path):
        from pathlib import Path  # noqa: PLC0415

        from openflight.camera import tester_server as ts  # noqa: PLC0415

        return ts._reference_ball_camera(  # pylint: disable=protected-access
            ts.ARMS["arm6"], Path(self.RIG), self.TILT, artifact_path, placement_path
        )

    def test_a_placement_for_the_loaded_rig_builds(self, tmp_path):
        camera = self._camera(*self._files(tmp_path))
        assert camera.source == "calibrated_candidate_unqualified"

    def test_the_lis3dh_roll_is_recorded_not_applied(self, tmp_path, monkeypatch):
        # C8: the calibrated path used to turn by the raw LIS3DH roll
        monkeypatch.setattr(self, "TILT", {**self.TILT, "roll_deg": -2.9})
        camera = self._camera(*self._files(tmp_path))
        assert camera.ray_model.snapshot["observed_pose_deg"]["roll"] == 0.0

    def test_a_placement_for_another_rig_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="rig_geometry_sha256"):
            self._camera(*self._files(tmp_path, rig_geometry_sha256="another-rig"))

    def test_origins_that_contradict_the_rigs_iwr_offset_are_refused(self, tmp_path):
        with pytest.raises(ValueError, match="rig offset"):
            self._camera(*self._files(tmp_path, radar_origin_lfu=[0.0, -0.08, 0.051]))
