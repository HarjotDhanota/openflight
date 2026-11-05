"""G2 C3 (4 Nov 2026): no frame at or after the contact enters a delivery window.

The preferred path window was (impact - 2, impact + 1): on Oct 27 shot 1 that is f41->f44,
and f44 is 1.1-1.8 ms after contact with the heel already on the mat (its descent stopped:
-5 mm against -28 mm a frame before). Path, attack angle and the plane now read only frames
before the contact; the preferred path window is the same three-frame span ending at the last
frame the ball rested in, (impact - 3, impact).
"""

from __future__ import annotations

import numpy as np
import pytest

from openflight.camera import club_delivery as cd
from openflight.camera.club_delivery import ApproachPairEstimate, _preferred_path_estimate
from tests.test_camera_club_contact_tangent import _shot1_club


def test_no_window_reaches_past_the_last_rest_frame():
    for first, last in cd.APPROACH_PATH_OFFSETS:
        assert first < last <= 0, (first, last)
    assert cd.PREFERRED_PATH_OFFSETS[0] == (-3, 0)


def test_the_preferred_path_window_is_the_pre_contact_span():
    pre = ApproachPairEstimate(3.0, -4.0, 1.0, 1.0, 12)
    earlier = ApproachPairEstimate(7.0, -4.0, 1.0, 1.0, 12)
    assert _preferred_path_estimate({(-4, -1): earlier}) is earlier
    assert _preferred_path_estimate({(-4, -1): earlier, (-3, 0): pre}) is pre


@pytest.fixture(scope="module")
def shot1():
    case = _shot1_club()
    return case, cd.estimate_chained_delivery(case["frames"], case["host"], **case["kwargs"])


def test_oct27_shot1_reads_its_path_on_f40_to_f43(shot1):
    case, result = shot1
    assert result.path_window_frames == (40, 43)
    stamps = case["kwargs"]["sensor_timestamp_ns"] / 1e9
    frames = sorted(int(np.argmin(np.abs(stamps - p.t_s))) for p in result.head_positions)
    assert frames == [39, 40, 41, 42, 43]  # f44 (after contact) is in no window
    assert all(p.t_s < result.contact_t_s for p in result.head_positions)
    assert result.club_path_deg is not None


def test_a_window_whose_last_frame_is_not_before_the_contact_is_refused():
    stamps = np.arange(50, dtype=np.int64) * 3_617_000
    contact_s = float(stamps[43]) / 1e9  # an instant AT the f43 exposure
    assert cd._window_before_contact((40, 43), stamps, contact_s) is False
    assert cd._window_before_contact((39, 42), stamps, contact_s) is True
    assert cd._window_before_contact((41, 44), stamps, contact_s + 1e-3) is False
