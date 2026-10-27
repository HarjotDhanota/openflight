"""Enclosure geometry: the static file."""

from __future__ import annotations

import dataclasses
import json
import math

import pytest

from openflight.rig_geometry import (
    EnclosureSetup,
    RigGeometry,
    camera_rdf_offset_to_target_lfu,
    levm_phase_centre_offset_mm,
    levm_vertical_phase_centre_offset_mm,
)

V3 = "config/enclosure_v3_rig_geometry.json"


def rig(**overrides) -> RigGeometry:
    base = dict(
        focal_px=466.6667,
        image_width=320,
        image_height=200,
        boresight_pitch_deg=0.0,
        ops_offset_mm=(-85.0, 47.0, -20.0),
        iwr_offset_mm=(0.0, 44.0, -30.0),
        mic_offset_mm=(-80.0, 0.0, 0.0),
        provenance="test",
        lens_height_above_floor_mm=95.0,
        iwr_boresight_pitch_deg=10.0,
        iwr_board_rotation_deg=None,
        ops_boresight_pitch_deg=10.0,
        housing_tilt_deg=0.0,
        lis3dh_mount_pitch_deg=None,
        lis3dh_mount_roll_deg=None,
        lis3dh_mount_yaw_deg=None,
    )
    base.update(overrides)
    return RigGeometry(**base)


class TestTheFile:
    def test_snapshot_identity_survives_json_formatting_but_tracks_forward_offset(self, tmp_path):
        original = rig()
        path = tmp_path / "rig.json"
        path.write_text(json.dumps(dataclasses.asdict(original), sort_keys=True, indent=4))

        assert RigGeometry.from_json(path).snapshot() == original.snapshot()
        moved = dataclasses.replace(original, iwr_offset_mm=(0.0, 44.0, -40.0))
        assert moved.snapshot()["sha256"] != original.snapshot()["sha256"]

    def test_json_round_trips_tuples_and_nones(self, tmp_path):
        path = tmp_path / "rig.json"
        original = rig(ops_offset_mm=None)
        original.to_json(path)
        loaded = RigGeometry.from_json(path)
        assert loaded == original
        assert isinstance(loaded.iwr_offset_mm, tuple)
        assert loaded.ops_offset_mm is None

    @pytest.mark.parametrize("field", ["iwr_boresight_pitch_deg", "iwr_board_rotation_deg"])
    def test_a_missing_field_is_refused_not_defaulted(self, tmp_path, field):
        data = dataclasses.asdict(rig())
        data.pop(field)
        path = tmp_path / "old.json"
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match=field):
            RigGeometry.from_json(path)

    def test_an_unknown_field_is_refused(self, tmp_path):
        data = {**dataclasses.asdict(rig()), "radar_height_mm": 51.0}
        path = tmp_path / "extra.json"
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="radar_height_mm"):
            RigGeometry.from_json(path)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("focal_px", None),
            ("focal_px", -466.0),
            ("image_width", 320.5),
            ("image_height", True),
            ("boresight_pitch_deg", None),
            ("provenance", None),
            ("iwr_offset_mm", [0.0, 44.0]),
            ("iwr_offset_mm", [0.0, "44", -30.0]),
            ("lens_height_above_floor_mm", "95"),
            ("housing_tilt_deg", float("nan")),
            ("iwr_board_rotation_deg", "90"),
        ],
    )
    def test_a_malformed_value_is_refused(self, tmp_path, field, value):
        data = {**dataclasses.asdict(rig()), field: value}
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match=field):
            RigGeometry.from_json(path)

    def test_the_dataclass_has_no_silent_defaults(self):
        with pytest.raises(TypeError):
            RigGeometry(focal_px=466.6667)  # pylint: disable=no-value-for-parameter


class TestTheEnclosureSetup:
    def test_lateral_sign_is_camera_relative_to_radar(self):
        setup = rig(iwr_offset_mm=(75.0, 44.0, 0.0)).enclosure_setup()
        assert setup.camera_lateral_offset_m == pytest.approx(-0.075)

    @pytest.mark.parametrize(
        ("offset_mm", "lfu_m"),
        [
            ((0.0, 44.0, -30.0), (0.0, -0.030, -0.044)),
            ((75.0, 44.0, -30.0), (0.075, -0.030, -0.044)),  # right of the lens: positive
            ((-85.0, 47.0, -20.0), (-0.085, -0.020, -0.047)),  # the OPS, target-left
        ],
    )
    def test_camera_rdf_offset_has_explicit_target_lfu_axes(self, offset_mm, lfu_m):
        assert camera_rdf_offset_to_target_lfu(offset_mm) == pytest.approx(lfu_m)

    def test_missing_pieces_are_named_not_defaulted(self):
        setup = rig(lens_height_above_floor_mm=None, iwr_boresight_pitch_deg=None).enclosure_setup()
        assert setup.camera_mount_height_m is None
        assert setup.radar_height_m is None
        assert setup.iwr_tilt_deg is None
        assert set(setup.missing) == {"lens_height_above_floor_mm", "iwr_boresight_pitch_deg"}

    def test_no_iwr_offset_means_no_lateral_and_no_radar_height(self):
        setup = rig(iwr_offset_mm=None).enclosure_setup()
        assert setup.camera_lateral_offset_m is None
        assert setup.camera_forward_offset_m is None
        assert setup.radar_height_m is None
        assert "iwr_offset_mm" in setup.missing

    def test_as_dict_is_json_safe(self):
        json.dumps(rig().enclosure_setup().as_dict())

    def test_the_tee_lateral_offset_comes_from_the_rig_not_july(self):
        v3 = RigGeometry.from_json(V3).enclosure_setup()
        assert v3.tee_lateral_offset_m == pytest.approx(0.001858, abs=1e-5)
        offset = rig(iwr_offset_mm=(75.0, 44.0, 0.0)).enclosure_setup()
        assert offset.tee_lateral_offset_m == pytest.approx(-0.075)
        assert offset.as_dict()["tee_lateral_offset_m"] == pytest.approx(-0.075)
        assert rig(iwr_offset_mm=None).enclosure_setup().tee_lateral_offset_m is None


class TestTheExpectedInclinometerOrientation:
    def test_a_file_without_mount_angles_assumes_the_board_is_parallel(self):
        expected = rig(housing_tilt_deg=10.0).expected_inclinometer_orientation()
        assert expected.pitch_deg == pytest.approx(10.0)
        assert expected.roll_deg == pytest.approx(0.0)
        assert "ASSUMED" in expected.provenance["pitch_deg"]
        assert set(expected.missing) == {"lis3dh_mount_pitch_deg", "lis3dh_mount_roll_deg"}

    def test_measured_mount_angles_are_added_and_credited(self):
        expected = rig(
            housing_tilt_deg=10.0, lis3dh_mount_pitch_deg=0.75, lis3dh_mount_roll_deg=0.2
        ).expected_inclinometer_orientation()
        assert expected.pitch_deg == pytest.approx(10.75)
        assert expected.roll_deg == pytest.approx(0.2)
        assert "from the rig file" in expected.provenance["pitch_deg"]
        assert expected.missing == ()

    def test_without_a_housing_tilt_there_is_no_expected_pitch(self):
        expected = rig(housing_tilt_deg=None).expected_inclinometer_orientation()
        assert expected.pitch_deg is None
        assert "housing_tilt_deg" in expected.missing
        assert expected.roll_deg == pytest.approx(0.0)

    def test_the_shipped_v3_file_expects_a_level_enclosure_and_a_turned_lis3dh(self):
        loaded = RigGeometry.from_json(V3)
        assert loaded.lis3dh_mount_yaw_deg == 180.0
        expected = loaded.expected_inclinometer_orientation()
        assert expected.pitch_deg == pytest.approx(0.0)
        assert expected.roll_deg == pytest.approx(0.0)
        assert expected.missing == ()

    def test_as_dict_is_json_safe(self):
        json.dumps(rig().expected_inclinometer_orientation().as_dict())


class TestTheRadarPhaseCentre:
    """The two-ray model wants the virtual array's vertical phase centre."""

    def test_levm_layout_puts_the_phase_centre_off_the_rx_row(self):
        assert levm_vertical_phase_centre_offset_mm(90.0) == pytest.approx(7.969, abs=0.01)
        assert levm_vertical_phase_centre_offset_mm(-90.0) == pytest.approx(-7.969, abs=0.01)
        assert levm_vertical_phase_centre_offset_mm(0.0) == pytest.approx(-1.858, abs=0.01)
        assert levm_vertical_phase_centre_offset_mm(180.0) == pytest.approx(1.858, abs=0.01)

    def test_the_v3_mount_puts_it_up_and_back_from_the_rx_row(self):
        # +90 deg, aimed 10 deg up: the board's "up" leans back
        right, down, forward = levm_phase_centre_offset_mm(90.0, 10.0)
        in_plane = levm_vertical_phase_centre_offset_mm(90.0)
        assert in_plane == pytest.approx(7.969, abs=0.01)
        assert -down == pytest.approx(in_plane * math.cos(math.radians(10.0)))
        assert -down == pytest.approx(7.85, abs=0.01)
        assert forward == pytest.approx(-1.38, abs=0.01)
        assert right == pytest.approx(-1.858, abs=0.01)

    def test_a_level_board_keeps_the_offset_in_its_plane(self):
        assert levm_phase_centre_offset_mm(90.0, 0.0) == pytest.approx(
            (-1.858, -7.969, 0.0), abs=0.01
        )

    def test_the_rig_derives_the_origin_from_its_rotation_and_aim(self):
        turned = rig(iwr_board_rotation_deg=90.0)
        assert turned.iwr_phase_centre_offset_mm == pytest.approx(
            (-1.858, -7.848, -1.384), abs=0.01
        )
        assert turned.iwr_origin_mm == pytest.approx((-1.858, 36.152, -31.384), abs=0.01)

    def test_without_the_rotation_or_the_aim_the_origin_is_the_rx_row(self):
        assert rig().iwr_phase_centre_offset_mm is None
        assert rig().iwr_origin_mm == (0.0, 44.0, -30.0)
        unaimed = rig(iwr_board_rotation_deg=90.0, iwr_boresight_pitch_deg=None)
        assert unaimed.iwr_phase_centre_offset_mm is None
        assert unaimed.iwr_origin_mm == (0.0, 44.0, -30.0)
        assert rig(iwr_offset_mm=None, iwr_board_rotation_deg=90.0).iwr_origin_mm is None

    def test_a_null_rotation_derives_the_live_inputs_from_the_rx_row(self):
        setup = rig().enclosure_setup()
        assert isinstance(setup, EnclosureSetup)
        assert setup.missing == ()
        assert setup.camera_mount_height_m == pytest.approx(0.095)
        assert setup.iwr_tilt_deg == 10.0
        assert setup.radar_height_m == pytest.approx(0.051)
        assert setup.radar_rx_row_height_m == pytest.approx(0.051)
        assert setup.camera_lateral_offset_m == pytest.approx(0.0)
        assert setup.camera_forward_offset_m == pytest.approx(0.030)
        assert setup.radar_height_reference == "iwr_rx_row_centre"
        assert setup.radar_phase_centre_offset_m is None
        assert setup.radar_phase_centre_status == "unknown_iwr_board_orientation"
        record = setup.as_dict()
        assert record["radar_height_reference"] == "iwr_rx_row_centre"
        assert record["radar_phase_centre_offset_m"] is None
        assert "missing" in record and "iwr_board_orientation" not in record["missing"]

    def test_the_v3_file_places_the_radar_at_the_phase_centre(self):
        setup = RigGeometry.from_json(V3).enclosure_setup()
        assert setup.camera_mount_height_m == pytest.approx(0.095, abs=5e-4)
        assert setup.camera_lateral_offset_m == pytest.approx(0.0, abs=2.5e-3)
        assert setup.iwr_tilt_deg == pytest.approx(10.0)
        assert setup.radar_height_m == pytest.approx(0.05225, abs=1e-5)
        assert setup.radar_rx_row_height_m == pytest.approx(0.0444)
        assert setup.radar_phase_centre_offset_m == pytest.approx(0.00785, abs=1e-5)
        assert setup.radar_height_m - setup.radar_rx_row_height_m == pytest.approx(
            setup.radar_phase_centre_offset_m
        )
        assert setup.camera_forward_offset_m == pytest.approx(0.03138, abs=1e-5)
        assert setup.radar_height_reference == "iwr_virtual_array_phase_centre"
        assert setup.radar_phase_centre_status == "derived_from_board_rotation"
        record = setup.as_dict()
        assert record["radar_height_reference"] == "iwr_virtual_array_phase_centre"
        assert record["radar_rx_row_height_m"] == pytest.approx(0.0444)
        assert record["radar_phase_centre_offset_m"] == pytest.approx(0.00785, abs=1e-5)
        assert setup.missing == ()


class TestTheOpsPosition:
    def test_the_v3_ops_sits_85_mm_left_of_the_teed_ball(self):
        # OPS (-85, 47, -20) and IWR (0, 44, -30) in camera right/down/forward mm
        rig_v3 = RigGeometry.from_json(V3)
        radar_height = rig_v3.enclosure_setup().radar_height_m
        forward, lateral, above = rig_v3.ops_ball_geometry_m(
            tee_slant_range_m=1.30, ball_height_m=0.02135, radar_height_m=radar_height
        )
        ball_forward = math.sqrt(1.30**2 - (0.02135 - radar_height) ** 2 - 0.001858**2)
        assert lateral == pytest.approx(0.085)
        assert forward == pytest.approx(ball_forward - (0.03138 - 0.020), abs=1e-5)
        assert above == pytest.approx(0.02135 - (0.051 - 0.003))

    def test_the_ops_height_does_not_move_with_the_radar_origin(self):
        heights = []
        for rotation in (None, 90.0):
            turned = rig(iwr_board_rotation_deg=rotation)
            radar_height = turned.enclosure_setup().radar_height_m
            _forward, lateral, above = turned.ops_ball_geometry_m(
                tee_slant_range_m=1.30, ball_height_m=0.02135, radar_height_m=radar_height
            )
            heights.append(0.02135 - above)
            assert lateral == pytest.approx(0.085)
        assert heights == pytest.approx([0.048, 0.048])

    def test_without_an_ops_offset_there_is_no_ops_geometry(self):
        assert (
            rig(ops_offset_mm=None).ops_ball_geometry_m(
                tee_slant_range_m=1.3, ball_height_m=0.02, radar_height_m=0.05
            )
            is None
        )
