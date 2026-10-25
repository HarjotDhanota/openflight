"""OPS club speed on real 7-iron swings: read just before the ball, never after it.

The fixture holds the 33 raw rolling-buffer captures of the 24 Oct 2026 outdoor
arm5 session (7-iron, OPS243 behind the golfer), the kiosk's recorded speeds at
e31b3025, and an independent re-measurement made outside the processor from the
same I/Q: the club trace's ridge in a 4.3 ms window ending about 4.5 ms before the
ball line is established on a 256-sample spectrogram (``independent_*``).

At e31b3025 the club pick was anchored on the *strongest* ball reading. When that
reading came long after the ball first appeared, the pick landed after impact
(shot 20: 33 mph read at 82 ms, the ridge before the ball is ~60 mph) or fell back
to a magnitude pick early on the rising trace (shot 9). Shot 33 read ~4 mph low
because weak windows where the head's return had dropped out were averaged in.
"""

from pathlib import Path

import numpy as np
import pytest

from openflight.clubs import ClubType
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import IQCapture

FIXTURE = Path(__file__).parent / "fixtures" / "ops_club_pick" / "outdoor-test-1-arm5.npz"

# Gross mis-picks at e31b3025 (20: after impact; 9 and 33: ~4 mph low).
FIXED_SHOTS = (9, 20, 33)
# Shots where the kiosk already agreed with the independent ridge; must not regress.
GOOD_SHOTS = (2, 3, 4, 10, 11, 13, 17, 18, 19, 21, 22, 24, 26, 28, 29, 30, 31, 32)

# The two estimators read the same ridge at slightly different times: the
# independent one a single 4.3 ms window ~4.5 ms before the ball line, the
# processor a 0.7 quantile of the clean plateau before it. The ridge still rises
# ~1-1.5 mph per ms there, so 2 ms of placement is 2-3 mph. On the 18 shots the
# kiosk already got right the two agree to within 2.9 mph (sd 1.7), so 3 mph is
# the agreement floor between them; the mis-picks being fixed are 4-27 mph off.
INDEPENDENT_TOLERANCE_MPH = 3.0

# The independent ball onset is the centre of the first 256-sample window (16-sample
# hop) whose ball line is 15 dB over the spectrogram's noise. A strong ball crosses
# that while it fills only the window's tail, so the centre precedes the ball's
# first sample; the processor's own onset sits on a 32-sample grid. One 32-sample
# hop of slack covers the two grids, nothing more.
ONSET_GRID_SLACK_MS = 32 / 30000 * 1000.0

# Shot 7's independent onset (42.1 ms) is the club ridge's spill into the ball band:
# its club runs to within ~5 mph of the ball. Resolved at 256 samples, the first
# window with a peak of its own within 1.5 mph of the ball (at 6x the noise) is
# centred at 49.2 ms; that is the reference for this shot.
RESOLVED_BALL_ONSET_MS = {7: 49.2}


@pytest.fixture(scope="module")
def archive():
    with np.load(FIXTURE) as data:
        return {key: data[key] for key in data.files}


@pytest.fixture(scope="module")
def processed(archive):
    processor = RollingBufferProcessor()
    club_type = ClubType(str(archive["club_type"]))
    results = {}
    for index, shot in enumerate(archive["shot"]):
        capture = IQCapture(
            sample_time=float(archive["sample_time"][index]),
            trigger_time=float(archive["trigger_time"][index]),
            i_samples=archive["i_samples"][index].tolist(),
            q_samples=archive["q_samples"][index].tolist(),
        )
        results[int(shot)] = processor.process_capture(capture, club_type=club_type)
    return results


def _value(archive, key, shot):
    index = int(np.flatnonzero(archive["shot"] == shot)[0])
    return float(archive[key][index])


def _window_ms():
    return RollingBufferProcessor.WINDOW_SIZE / RollingBufferProcessor.SAMPLE_RATE * 1000.0


def test_the_fixture_is_small_and_holds_every_capture_of_the_session(archive):
    assert FIXTURE.stat().st_size < 600_000
    assert archive["shot"].tolist() == list(range(1, 34))
    assert archive["i_samples"].shape == (33, 4096)
    assert archive["q_samples"].shape == (33, 4096)


@pytest.mark.parametrize("shot", range(1, 34))
def test_ball_speed_is_unchanged_on_every_capture(archive, processed, shot):
    assert processed[shot].ball_speed_mph == pytest.approx(
        _value(archive, "recorded_ball_mph", shot), abs=1e-9
    )


@pytest.mark.parametrize("shot", FIXED_SHOTS + GOOD_SHOTS)
def test_club_speed_matches_the_independent_ridge(archive, processed, shot):
    independent = _value(archive, "independent_club_mph", shot)
    assert processed[shot].club_speed_mph == pytest.approx(
        independent, abs=INDEPENDENT_TOLERANCE_MPH
    )


@pytest.mark.parametrize("shot", range(1, 34))
def test_club_is_never_read_after_the_ball_line_is_established(archive, processed, shot):
    result = processed[shot]
    onset_ms = RESOLVED_BALL_ONSET_MS.get(shot, _value(archive, "independent_ball_onset_ms", shot))
    if result.club_speed_mph is None or np.isnan(onset_ms):
        return
    assert result.club_timestamp_ms + _window_ms() <= onset_ms + ONSET_GRID_SLACK_MS


@pytest.mark.parametrize("shot", range(1, 34))
def test_club_is_never_read_after_the_impact_estimate(processed, shot):
    result = processed[shot]
    if result.club_speed_mph is None or result.impact.timestamp_ms is None:
        return
    assert result.club_timestamp_ms + _window_ms() <= result.impact.timestamp_ms


def test_shot_20_reads_the_ridge_before_the_sound_trigger_not_the_club_after_impact(
    archive, processed
):
    result = processed[20]
    assert _value(archive, "recorded_club_timestamp_ms", 20) > result.capture.trigger_offset_ms
    assert result.club_timestamp_ms + _window_ms() < result.capture.trigger_offset_ms
    assert result.club_speed_mph > 55.0
