"""The camera head track on real swings (fork only: it loads field frames).

Harjot-outdoor-test-1 (dusk, OV9281 + 6 mm lens at 1280x800, 120 fps, the ball 1.91 m
out): shot 13 at 145 us shows the club in three pre-contact frames, shot 32 at 22 us in
two. The reference points are where the shaft's axis leaves the heel's sole, rendered
and checked by eye (shot 32's dim heel: the hosel's lower end). The angles are this
fixture's values, not validated against any reference.
"""

from pathlib import Path

import numpy as np
import pytest

from openflight.camera.head_track import (
    HOSEL_PIXEL_SIGMA,
    HeadTrackCamera,
    HeadTrackInputs,
    acoustic_contact_window,
    estimate_head_track,
)
from openflight.clubs import ClubType

FIXTURE = Path(__file__).parent / "fixtures" / "head_track" / "outdoor-test-1.npz"

# shot: (OPS radial club mph re-read by the current processor, {frame offset: hosel px})
SWINGS = {
    13: (56.7, {-2: (633.83, 211.71), -1: (703.71, 350.72), 0: (743.67, 421.81)}),
    32: (77.7, {-1: (623.42, 295.75), 0: (680.31, 402.69)}),
}


@pytest.fixture(scope="module")
def archive():
    with np.load(FIXTURE) as data:
        return {key: data[key] for key in data.files}


def _track(archive, swing: int):
    prefix = f"s{swing}_"
    frames = archive[prefix + "frames"]
    x0, y0 = (float(v) for v in archive[prefix + "crop_offset_xy"])
    bx, by = (float(v) for v in archive[prefix + "ball_xy"])
    shot = int(archive[prefix + "shot"])
    ops_mph, _points = SWINGS[shot]
    window = acoustic_contact_window(
        float(archive[prefix + "trigger_boottime_ns"]), float(archive[prefix + "gate_latency_ns"])
    )
    camera = HeadTrackCamera(
        focal_px=2000.0,
        pitch_deg=0.0,
        image_width_px=frames.shape[2],
        image_height_px=frames.shape[1],
        principal_point_px=(640.0 - x0, 400.0 - y0),
    )
    track = estimate_head_track(
        HeadTrackInputs(
            frames=frames,
            timestamps_ns=archive[prefix + "sensor_timestamp_ns"],
            exposure_us=archive[prefix + "exposure_us"],
            camera=camera,
            ball_px=(bx - x0, by - y0),
            ball_range_m=1.91,
            ops_club_speed_mph=ops_mph,
            contact_window_ns=window,
            club=ClubType.IRON_7,
        )
    )
    return shot, track, (x0, y0)


def test_the_fixture_is_small():
    assert FIXTURE.stat().st_size < 2_000_000


@pytest.mark.parametrize("swing", [1, 2])
def test_the_hosel_is_found_where_the_renders_put_it(archive, swing):
    shot, track, (x0, y0) = _track(archive, swing)
    _ops, expected = SWINGS[shot]

    assert track.status == "ok", track.notes
    found = {
        point["frame"] - track.last_rest_frame: (point["x_px"] + x0, point["y_px"] + y0)
        for point in track.track
    }
    assert found.keys() == expected.keys()
    # within the head track's own per-point sigma (HOSEL_PIXEL_SIGMA): the fixture's
    # background is a 3-frame median, the full clip's a 9-frame one
    for offset, (x, y) in expected.items():
        assert found[offset][0] == pytest.approx(x, abs=HOSEL_PIXEL_SIGMA)
        assert found[offset][1] == pytest.approx(y, abs=HOSEL_PIXEL_SIGMA)


def test_three_frames_measure_the_turn_and_two_lean_on_the_prior(archive):
    _shot, three, _ = _track(archive, 1)
    _shot, two, _ = _track(archive, 2)

    assert three.turning == "two_chords"
    assert 0.5 <= three.turn_rate_ratio <= 2.0
    assert two.turning == "arc_prior"
    assert two.attack_angle_sigma_deg > three.attack_angle_sigma_deg
    for track in (three, two):
        assert track.club_path_flag is None and track.attack_angle_flag is None
        assert abs(track.club_path_deg) < 15.0 and abs(track.attack_angle_deg) < 15.0
