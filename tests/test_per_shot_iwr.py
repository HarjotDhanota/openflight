"""The IWR runtime carries nothing from one shot to the next (D17).

It used to keep up to 50 earlier shots' accepted ball tracks and, when a shot had no
impact time of its own, fit them into a prior that picked this shot's track. A shot
without its own impact time now has none.
"""

import dataclasses
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from openflight.iwr6843 import runtime as runtime_module
from openflight.iwr6843.runtime import IWR6843Runtime, process_raw_capture


def test_the_runtime_keeps_no_earlier_shots_tracks():
    names = {field.name for field in dataclasses.fields(IWR6843Runtime)}

    assert "recovery_observations" not in names


def test_the_estimator_takes_no_earlier_shots_tracks():
    assert "recovery_observations" not in inspect.signature(process_raw_capture).parameters


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


def test_the_replay_snapshot_holds_no_earlier_shots_tracks():
    runtime = IWR6843Runtime(
        capture_monitor=SimpleNamespace(),
        calibration=SimpleNamespace(tee_range_m=1.5),
        net_range_m=None,
    )

    assert "recovery_observations" not in runtime.replay_config_snapshot()
