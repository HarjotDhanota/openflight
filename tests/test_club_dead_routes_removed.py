"""G2 C5 (4 Nov 2026): the club delivery's dead routes are gone.

``fuse_club_delivery`` (radar AoA + per-club offset + image trace), ``estimate_delivery_trace``
and ``delivery_from_feature_tracks`` had no production caller, and the AoA offset table
(``_IRON_OFFSET_ANCHORS`` / ``_DRIVER_OFFSET_DEG`` / ``aoa_offset_for_club``: TrackMan July
baseline minus the IWR radar's AoA candidate, one session, a -2 ms timing assumption since
replaced) corrected a different sensor's bias. Left in the module they looked live and could
be wired to the camera routes (research club-path-audit CODE-AUDIT s7).
"""

from __future__ import annotations

import pytest

from openflight.camera import club_delivery as cd

DEAD = (
    "fuse_club_delivery",
    "estimate_delivery_trace",
    "delivery_from_feature_tracks",
    "_clubhead_feature_tracks",
    "aoa_offset_for_club",
    "_IRON_OFFSET_ANCHORS",
    "_DRIVER_OFFSET_DEG",
    "_MEASURED_OFFSETS",
    "TraceResult",
    "FusedDelivery",
    "TRACE_ABS_RANGE_DEG",
)


@pytest.mark.parametrize("name", DEAD)
def test_the_dead_route_is_gone(name):
    assert not hasattr(cd, name)


def test_the_live_routes_remain():
    for name in (
        "estimate_chained_delivery",
        "_delivery_from_feature_pair",
        "camera_ops_delivery_from_feature_pair",
        "combine_approach_estimates",
        "camera_contact_time",
    ):
        assert callable(getattr(cd, name))
