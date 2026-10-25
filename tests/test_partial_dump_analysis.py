"""Analysis labels a saved partial IWR dump incomplete and never locates it as a capture."""

from __future__ import annotations

import pytest

from openflight import pipeline_check
from openflight.raw_radar_replay import locate_recorded_capture
from tests.test_iwr6843_partial_dumps import partial_event


def test_a_partial_dump_is_never_located_as_a_capture(tmp_path):
    event = partial_event(tmp_path)
    with pytest.raises(ValueError, match="incomplete"):
        locate_recorded_capture(event["partial_capture"]["path"], tmp_path)


def test_the_pipeline_check_names_the_kept_partial_dump(tmp_path):
    event = partial_event(tmp_path)

    verdict = pipeline_check.iwr_capture({}, event)

    assert verdict["status"] == pipeline_check.FAIL
    assert "ended early" in verdict["evidence"]
    assert f"{event['partial_capture']['bytes']} of" in verdict["evidence"]
    assert "incomplete" in verdict["evidence"]
