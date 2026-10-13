"""TM4 export loaders: the in-repo TrackMan normalized CSV and the SwingSync format.

The flight-model refit (E8a) fits carry, apex, landing angle and hang time, so the
loaders must read all four, in the units the fit uses (yards, feet, degrees,
seconds), from either export format, detected from the header.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ANALYSIS = _REPO_ROOT / "scripts" / "analysis"
if str(_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS))

from validate_ballistics import (  # noqa: E402
    detect_export_format,
    load_swingsync,
    load_tm_export,
    load_trackman,
)

TRACKMAN_CSV = _REPO_ROOT / "session_logs" / "OpenFlight-Test.Normalized.csv"

SWINGSYNC_HEADER = (
    "Shot number,Date,Classification,Club type,Club name,Carry,Total,Offline,"
    "Peak height,Hang time,Club speed,Ball speed,Horizontal Launch Angle,"
    "Vertical Launch angle,Landing angle,Backspin,Sidespin"
)


def _swingsync(tmp_path: Path, units: str, rows: list[str]) -> Path:
    path = tmp_path / "session.csv"
    path.write_text("\n".join([SWINGSYNC_HEADER, units, *rows]) + "\n", encoding="utf-8")
    return path


YARD_UNITS = ",,,,,[yds],[yds],[yds],[yds],[seconds],[mph],[mph],[°],[°],[°],[rpm],[rpm]"


def test_the_trackman_loader_reads_all_four_flight_outputs():
    shots = load_trackman(TRACKMAN_CSV)
    assert len(shots) == 24
    first = shots[0]
    assert first.club_raw == "7 Iron"
    assert first.carry_yards == pytest.approx(177.21028090888305)
    assert first.apex_feet == pytest.approx(89.41193327997439)
    assert first.landing_angle_deg == pytest.approx(45.19227948626146)
    assert first.hang_time_s == pytest.approx(6.113327941894445)
    assert first.export_format == "trackman_normalized"
    assert all(s.landing_angle_deg is not None and s.hang_time_s is not None for s in shots)


def test_the_trackman_export_is_detected_by_its_header():
    assert detect_export_format(TRACKMAN_CSV) == "trackman_normalized"
    assert len(load_tm_export(TRACKMAN_CSV)) == 24


def test_a_swingsync_export_is_detected_and_converted(tmp_path):
    path = _swingsync(
        tmp_path,
        YARD_UNITS,
        [
            "1,2026-10-12T14:25:24Z,Fade,Pitching Wedge,Default,56.058,67.5,-1.0,"
            "9.591,3.030,53.2,56.575,-4.140,27.860,38.040,6404,1277",
            "2,2026-10-12T14:26:24Z,Top,7 Iron,Default,30.621,40.0,2.0,"
            "1.301,0.970,60.0,69.057,-11.200,9.270,10.120,-160,-879",
        ],
    )
    assert detect_export_format(path) == "swingsync"
    shots = load_tm_export(path)
    assert [s.club_raw for s in shots] == ["Pitching Wedge", "7 Iron"]
    pw, topped = shots
    assert pw.export_format == "swingsync"
    assert pw.ball_speed_mph == pytest.approx(56.575)
    assert pw.launch_v_deg == pytest.approx(27.86)
    assert pw.launch_h_deg == pytest.approx(-4.14)
    # total spin and the axis tilt from the back/side split
    assert pw.spin_rpm == pytest.approx(math.hypot(6404, 1277))
    assert pw.spin_axis_deg == pytest.approx(math.degrees(math.atan2(1277, 6404)))
    assert pw.carry_yards == pytest.approx(56.058)
    assert pw.apex_feet == pytest.approx(9.591 * 3.0)  # peak height is in yards
    assert pw.landing_angle_deg == pytest.approx(38.04)
    assert pw.hang_time_s == pytest.approx(3.03)
    assert pw.backspin_rpm == pytest.approx(6404)
    # topspin: the axis points past 90 degrees, the magnitude is still positive
    assert topped.spin_rpm == pytest.approx(math.hypot(160, 879))
    assert abs(topped.spin_axis_deg) > 90.0
    assert topped.backspin_rpm == pytest.approx(-160)


def test_a_metric_swingsync_export_is_converted_to_yards_feet_and_mph(tmp_path):
    units = ",,,,,[m],[m],[m],[m],[seconds],[km/h],[km/h],[°],[°],[°],[rpm],[rpm]"
    path = _swingsync(
        tmp_path,
        units,
        ["1,2026-10-12,x,7 Iron,Default,150.0,160,0,30.0,5.5,140,180.0,0,18,45,6000,0"],
    )
    (shot,) = load_swingsync(path)
    assert shot.carry_yards == pytest.approx(150.0 / 0.9144)
    assert shot.apex_feet == pytest.approx(30.0 / 0.3048)
    assert shot.ball_speed_mph == pytest.approx(180.0 / 1.609344)


def test_an_unknown_unit_is_refused_rather_than_guessed(tmp_path):
    units = ",,,,,[furlong],[yds],[yds],[yds],[seconds],[mph],[mph],[°],[°],[°],[rpm],[rpm]"
    path = _swingsync(
        tmp_path, units, ["1,d,x,7 Iron,Default,150,160,0,30,5.5,90,120,0,18,45,6000,0"]
    )
    with pytest.raises(ValueError, match="furlong"):
        load_swingsync(path)


def test_an_unrecognised_export_is_refused(tmp_path):
    path = tmp_path / "other.csv"
    path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="format"):
        load_tm_export(path)
