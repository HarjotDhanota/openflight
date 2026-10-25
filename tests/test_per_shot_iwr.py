"""The IWR runtime carries nothing from one shot to the next.

A shot without its own impact time has none: no prior is built from earlier shots.
"""

from types import SimpleNamespace
from unittest.mock import patch

from openflight.iwr6843 import runtime as runtime_module
from openflight.iwr6843.runtime import process_raw_capture


def test_a_shot_without_its_own_impact_time_gives_the_club_search_none():
    measurement = SimpleNamespace(impact_t_s=None, tdm_sign_used=1, horizontal_deg=None)
    seen = {}

    def club_path(*_args, impact_t_s, **_kwargs):
        seen["impact_t_s"] = impact_t_s
        return SimpleNamespace(status="rejected_no_impact")

    with (
        patch.object(runtime_module, "prepare_lcmf_capture"),
        patch.object(runtime_module, "estimate_lcmf_v1", return_value=measurement),
        patch.object(runtime_module, "ops_guided_measurement", return_value=measurement),
        patch.object(runtime_module, "estimate_club_path", side_effect=club_path),
    ):
        _measurement, club = process_raw_capture(
            b"",
            SimpleNamespace(),
            ball_speed_mph=100.0,
            club="7-iron",
            club_speed_mph=75.0,
            net_range_m=None,
            tx_order="normal",
            tdm_sign_policy="positive",
            azimuth_offset_deg=0.0,
            horizontal_phase_reference_rad=None,
            club_window_policy=runtime_module.ClubWindowPolicy(),
            club_impact_correction_s=-0.002,
        )

    assert seen["impact_t_s"] is None
    assert "recovered" not in club.status
