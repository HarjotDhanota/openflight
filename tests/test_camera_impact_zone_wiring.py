"""The impact zone reaches a shot record only behind a flag that is off.

It is a CONSISTENCY reading against one annotator's hand marks, on a face-centre
CONVENTION that puts the ball 30 mm heel-ward of centre on nearly every swing.
Nothing about it is validated against an independent instrument, so it ships
withheld unless somebody turns it on, and it ships withheld even then unless a
template exists for the club that was actually hit.

There is no UI. The field is a dict carrying the reading, its status, and the
convention that produced it, so a reader cannot pick the number up without the
caveat attached to it.
"""

from __future__ import annotations

import numpy as np
import pytest

from openflight.camera import club_delivery
from openflight.camera.club_motion import ReferenceBall
from openflight.camera.clubpose import head_outline as outline, impact_zone as zone

FPS = 467.6
TRIGGER_INDEX = 71
HEIGHT, WIDTH = 200, 320
BALL = ReferenceBall(x=170.0, y=146.0, diameter_px=11.9, area_px=111)


class _Track:
    """The duck type `fusion.ranges_from_track` and this wiring read."""

    speed_ms = 32.5
    quad_bins = None

    def speed_ms_at(self, _t_s, _range_res_m):
        """Local radial speed; the straight-line slope when there is no refit."""
        return self.speed_ms


class _Geometry:
    range_res_m = 0.0469


class _RangeEvidence:
    track = _Track()
    geometry = _Geometry()
    impact_t_s = 0.05


def _frames() -> np.ndarray:
    """A still bright ball and a dark head arriving at it, as the capture sees it."""
    frames = np.full((99, HEIGHT, WIDTH), 90, dtype=np.uint8)
    grid_y, grid_x = np.mgrid[0:HEIGHT, 0:WIDTH]
    radius = int(round(BALL.diameter_px / 2.0))
    disc = (grid_x - BALL.x) ** 2 + (grid_y - BALL.y) ** 2 <= radius**2
    frames[:, disc] = 235
    contact = zone.contact_frame_from_trigger(TRIGGER_INDEX, FPS)
    for index in range(64, 99):
        x = BALL.x - 9.0 * (contact - index)
        top, left = int(round(BALL.y + 4.0 - 8)), int(round(x - 15))
        frames[index, top : top + 16, left : left + 30] = 25
        frames[index, disc] = 235
    return frames


def _timestamps() -> np.ndarray:
    return (np.arange(99, dtype=np.int64) * int(round(1e9 / FPS))) + 1_000_000_000


@pytest.fixture(name="templates")
def _templates(tmp_path):
    template = outline.bootstrap_outline_from_category("7-iron")
    template.save(tmp_path / "7-iron.npz")
    return tmp_path


def _estimate(**overrides):
    kwargs = {
        "frames": _frames(),
        "host_timestamp_ns": _timestamps(),
        "trigger_index": TRIGGER_INDEX,
        "range_evidence": _RangeEvidence(),
        "club": "7-iron",
        "template_dir": None,
    }
    kwargs.update(overrides)
    return club_delivery.estimate_impact_zone(**kwargs)


class TestTheContactInstant:
    """The trigger is the SOUND arriving, not the ball being hit."""

    def test_contact_is_the_trigger_walked_back_by_the_flight_time(self):
        contact = zone.contact_frame_from_trigger(TRIGGER_INDEX, FPS)

        assert contact < TRIGGER_INDEX + 1
        assert (TRIGGER_INDEX + 1) - contact == pytest.approx(1.575 / 343.0 * FPS, abs=1e-9)

    def test_it_is_about_two_frames_on_the_shipped_rig(self):
        gap = (TRIGGER_INDEX + 1) - zone.contact_frame_from_trigger(TRIGGER_INDEX, FPS)

        assert gap == pytest.approx(2.15, abs=0.1)

    def test_a_non_physical_frame_rate_is_refused(self):
        with pytest.raises(ValueError):
            zone.contact_frame_from_trigger(TRIGGER_INDEX, 0.0)


class TestItWithholdsWithoutItsEvidence:
    def test_no_camera_frames_withholds(self):
        result = _estimate(frames=None)

        assert result.status == "withheld"
        assert result.reason == "no_camera_frames"

    def test_no_trigger_withholds(self):
        result = _estimate(trigger_index=None)

        assert result.status == "withheld"
        assert result.reason == "no_camera_trigger_frame"

    def test_no_iwr_range_evidence_withholds(self):
        """The outline's SCALE is the radar's range. Without it there is no size."""
        result = _estimate(range_evidence=None)

        assert result.status == "withheld"
        assert result.reason == "no_iwr_range_evidence"

    def test_no_template_directory_withholds(self, templates):
        result = _estimate(template_dir=None)

        assert result.status == "withheld"
        assert result.reason == "no_impact_zone_template_directory"

    def test_no_template_for_the_selected_club_withholds_and_names_it(self, templates):
        result = _estimate(template_dir=templates, club="driver")

        assert result.status == "withheld"
        assert "driver" in result.reason

    def test_an_unknown_club_withholds_rather_than_guessing_one(self, templates):
        result = _estimate(template_dir=templates, club=None)

        assert result.status == "withheld"
        assert result.reason == "no_club_selected"

    def test_timestamps_that_cannot_give_a_frame_rate_withhold(self, templates):
        result = _estimate(template_dir=templates, host_timestamp_ns=np.zeros(99, dtype=np.int64))

        assert result.status == "withheld"
        assert result.reason == "camera_frame_rate_not_recoverable"


class TestItReadsWhenEverythingIsThere:
    def test_a_result_is_produced_and_carries_its_provenance(self, templates):
        result = _estimate(template_dir=templates)

        assert isinstance(result, zone.ImpactZoneResult)
        assert result.club == "7-iron"
        assert result.template_source == "self-built"
        assert "CONVENTION" in result.centre_convention

    def test_the_frames_are_un_mirrored_when_the_capture_mirrors_them(self, templates):
        straight = _estimate(template_dir=templates)
        mirrored = _estimate(
            template_dir=templates,
            frames=_frames()[:, :, ::-1],
            mirror_horizontal=True,
        )

        assert straight.status == mirrored.status
        if straight.heel_toe_mm is not None:
            assert mirrored.heel_toe_mm == pytest.approx(straight.heel_toe_mm, abs=1e-6)

    def test_the_reading_survives_a_json_round_trip(self, templates):
        import json

        payload = json.loads(json.dumps(_estimate(template_dir=templates).as_dict()))

        assert payload["status"] in {"ok", "withheld"}
        assert payload["high_low_status"] == "experimental_unvalidated"


class TestTheServerFlag:
    def test_the_feature_is_off_by_default(self):
        from openflight import server

        assert server.experimental_impact_zone is False
        assert server.experimental_impact_zone_templates is None

    def test_the_settings_helper_reports_disabled_by_default(self):
        from openflight import server

        settings = server.impact_zone_settings()

        assert settings["enabled"] is False
        assert settings["template_dir"] is None

    def test_a_shot_carries_the_field_and_it_starts_empty(self):
        from openflight.launch_monitor import Shot

        shot = Shot(timestamp=0.0, ball_speed_mph=100.0)

        assert hasattr(shot, "experimental_impact_zone")
        assert shot.experimental_impact_zone is None

    def test_the_disabled_reason_is_what_a_reader_sees(self):
        from openflight import server

        assert server.impact_zone_settings()["enabled"] is False
        assert zone.withheld("impact_zone_disabled").as_dict()["status"] == "withheld"
