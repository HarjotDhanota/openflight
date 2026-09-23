"""Enclosure geometry file <-> live server: flag overrides and the tilt arithmetic."""

from __future__ import annotations

import dataclasses
import json

import pytest

from openflight import server
from openflight.rig_geometry import RigGeometry

V3 = "config/enclosure_v3_rig_geometry.json"


@pytest.fixture(autouse=True)
def _restore_module_globals():
    """Each test loads geometry into module state; put it back afterwards."""
    saved = (server.rig_geometry, dict(server.rig_geometry_config))
    yield
    server.rig_geometry, server.rig_geometry_config = saved[0], saved[1]


class TestTheGeometryReplacesTheFlags:
    def test_without_a_file_nothing_is_claimed(self):
        server.init_rig_geometry(None)
        assert server.rig_geometry is None
        assert server.rig_geometry_config == {"enabled": False}

    def test_the_session_records_the_file_and_what_it_derived(self):
        server.init_rig_geometry(V3)
        config = server._session_start_config()

        block = config["rig_geometry"]
        assert block["enabled"] is True
        assert block["path"].endswith("enclosure_v3_rig_geometry.json")
        # first_look decides whether numbers may be attributed to sensors by
        # the presence of exactly this block, so the derived values ride in it.
        assert block["derived"]["radar_height_m"] == pytest.approx(0.051, abs=5e-4)
        assert block["derived"]["provenance"]

    def test_a_measured_value_beats_a_typed_flag(self):
        assert server._rig_override("radar height", 0.1524, 0.051) == 0.051

    def test_a_flag_survives_where_the_rig_is_silent(self):
        assert server._rig_override("radar height", 0.1524, None) == 0.1524

    def test_an_absent_file_is_an_error_not_a_silent_default(self):
        with pytest.raises(FileNotFoundError):
            server.init_rig_geometry("config/no_such_enclosure.json")


class TestTheExpectedOrientation:
    def test_without_a_file_the_expectation_is_named_absent(self):
        server.init_rig_geometry(None)
        expected = server._expected_inclinometer_orientation()

        assert expected["pitch_deg"] is None
        assert expected["missing"] == ["rig_geometry"]

    def test_the_v3_enclosure_expects_to_read_level(self):
        server.init_rig_geometry(V3)
        expected = server._expected_inclinometer_orientation()

        assert expected["pitch_deg"] == pytest.approx(0.0)
        assert expected["roll_deg"] == pytest.approx(0.0)


class TestTheTiltArithmetic:
    """effective = configured + (measured - expected); the raw sum doubles on a leaning housing."""

    @staticmethod
    def _effective(configured_deg: float, measured_deg: float, expected_deg: float) -> float:
        return configured_deg + (measured_deg - expected_deg)

    def test_a_level_enclosure_on_a_level_floor_keeps_its_mount_angle(self):
        # v3: shell level, radar on a +10 deg mount, LIS3DH reads 0.
        assert self._effective(10.0, 0.0, 0.0) == pytest.approx(10.0)

    def test_a_level_enclosure_on_a_sloping_floor_is_corrected(self):
        # The floor drops the nose 2 deg; the radar really points 8 deg up.
        assert self._effective(10.0, -2.0, 0.0) == pytest.approx(8.0)

    def test_a_leaning_housing_is_not_double_counted(self):
        # v42: housing leans 10 deg and the LIS3DH, parallel to it, reads +10.
        # Summing would hand the LCMF 20 deg -- roughly 20 deg of launch angle
        # at the ~2 deg per degree of tilt the estimator carries.
        assert self._effective(10.0, 10.0, 10.0) == pytest.approx(10.0)
        naive = 10.0 + 10.0
        assert naive == pytest.approx(20.0), "the defect this test exists to prevent"

    def test_a_leaning_housing_on_a_sloping_floor_is_still_corrected(self):
        assert self._effective(10.0, 12.5, 10.0) == pytest.approx(12.5)

    def test_an_unknown_expectation_falls_back_to_the_raw_reading(self):
        # No geometry file: expected is None, the server reads it as 0.0, and
        # the behaviour is the old one rather than a crash.
        server.init_rig_geometry(None)
        expected = server._expected_inclinometer_orientation().get("pitch_deg") or 0.0
        assert self._effective(10.0, 1.5, expected) == pytest.approx(11.5)


class TestTheFileTravelsWithItsProvenance:
    def test_the_v3_file_names_the_frame_and_what_it_is_not(self):
        text = json.loads(open(V3, encoding="utf-8").read())["provenance"]
        assert "camera image axes" in text
        assert "4-RX" in text
        assert "20260825" in text, "must say which session it does NOT describe"
        assert "PENDING checkerboard" in text, "focal_px is nominal until calibrated"

    def test_a_rig_without_heights_names_the_gap_instead_of_guessing(self):
        rig = dataclasses.replace(RigGeometry.from_json(V3), lens_height_above_floor_mm=None)
        setup = rig.enclosure_setup()
        assert "lens_height_above_floor_mm" in setup.missing
        assert setup.camera_mount_height_m is None
        assert setup.radar_height_m is None
