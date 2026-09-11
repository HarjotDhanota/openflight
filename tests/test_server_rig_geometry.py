"""The server takes its geometry from the enclosure file, and says when it does."""

from __future__ import annotations

import logging

import pytest

from openflight import server


@pytest.fixture(autouse=True)
def _reset_rig():
    yield
    server.init_rig_geometry(None)


def test_no_file_means_the_flags_stand_and_the_log_says_disabled():
    server.init_rig_geometry(None)

    assert server.rig_geometry is None
    assert server._session_start_config()["rig_geometry"] == {"enabled": False}
    assert server._rig_override("radar height", 0.1651, None) == 0.1651


def test_the_v42_file_is_loaded_derived_and_logged():
    server.init_rig_geometry("config/enclosure_v42_rig_geometry.json")

    block = server._session_start_config()["rig_geometry"]
    assert block["enabled"] is True
    assert block["path"].endswith("enclosure_v42_rig_geometry.json")
    derived = block["derived"]
    assert derived["camera_mount_height_m"] == pytest.approx(0.2207, abs=5e-4)
    assert derived["radar_height_m"] == pytest.approx(0.0556, abs=5e-4)
    assert derived["iwr_tilt_deg"] == pytest.approx(10.0)
    assert derived["missing"] == []
    assert "20260825" in derived["provenance"]


def test_the_file_overrides_a_flag_and_announces_it(caplog):
    with caplog.at_level(logging.INFO, logger=server.logger.name):
        value = server._rig_override("radar height", 0.1651, 0.0556)

    assert value == 0.0556
    assert any("overrides command-line" in record.message for record in caplog.records)


def test_an_equal_flag_is_not_announced_as_an_override(caplog):
    with caplog.at_level(logging.INFO, logger=server.logger.name):
        assert server._rig_override("radar tilt", 10.0, 10.0) == 10.0

    assert not any("overrides" in record.message for record in caplog.records)


def test_a_missing_flag_takes_the_rig_value_quietly():
    assert server._rig_override("radar tilt", None, 10.0) == 10.0
