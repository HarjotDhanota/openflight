"""Named IWR capture plans for the self-trigger shot session differ only in the pre phase."""

import pytest

from openflight.camera import iwr_capture_plan as plans

# The shot session's profile lines that size every retained frame (joint_clock_active.py).
PROFILE = (
    "flushCfg",
    "dfeDataOutputMode 1",
    "channelCfg 15 7 0",
    "adcCfg 2 1",
    "profileCfg 0 60.0 7 3 38 0 0 100 1 128 4000 0 0 30",
    "chirpCfg 0 0 0 0 0 0 0 1",
    "chirpCfg 1 1 0 0 0 0 0 2",
    "chirpCfg 2 2 0 0 0 0 0 4",
    "frameCfg 0 2 12 20000 3 1 0",
    "captureFormat iq16",
    "phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1",
    "lowPower 0 0",
)


def test_default_is_todays_line_and_club51_is_the_reviewed_pre_window():
    assert plans.DEFAULT == "default"
    assert plans.names() == ("default", "club51")
    assert plans.phase_line("default") == "phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1"
    assert plans.phase_line("club51") == "phaseCaptureCfg 16 32 17 32 53 7 47 53 47 8 1"
    with pytest.raises(ValueError, match="unknown IWR capture plan"):
        plans.phase_line("club64")


def test_selecting_a_plan_replaces_only_the_phase_line():
    assert plans.commands(PROFILE, "default") == PROFILE
    club = plans.commands(PROFILE, "club51")
    changed = [(old, new) for old, new in zip(PROFILE, club) if old != new]
    assert changed == [
        (
            "phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1",
            "phaseCaptureCfg 16 32 17 32 53 7 47 53 47 8 1",
        )
    ]
    with pytest.raises(ValueError, match="exactly one phaseCaptureCfg"):
        plans.commands(PROFILE[:-2], "club51")


def test_only_the_pre_phase_differs_between_plans():
    phases = {name: plans.phases(plans.phase_line(name)) for name in plans.names()}
    for name, phase in phases.items():
        assert phase["impact"] == phases["default"]["impact"], name
        assert phase["ball"] == phases["default"]["ball"], name
    assert phases["club51"]["pre"] == {"start": 16, "bins": 32, "frames": 17}
    assert phases["default"]["pre"] == {"start": 20, "bins": 53, "frames": 9}


@pytest.mark.parametrize(
    "name,frames,used",
    [
        # (53x9 + 53x7 + 53x8) bin-frames x 576 bytes
        ("default", 24, 576 * 53 * 24),
        # (32x17 + 53x7 + 53x8) bin-frames x 576 bytes = 753 KB of 768
        ("club51", 32, 576 * (32 * 17 + 53 * 7 + 53 * 8)),
    ],
)
def test_every_plan_fits_the_firmware_rules(name, frames, used):
    summary = plans.check(plans.commands(PROFILE, name))
    assert summary["bytes_per_bin_frame"] == 576  # IQ16, 3 TX x 12 loops x 4 RX x 4 bytes
    assert summary["frames"] == frames <= 64
    assert summary["bytes"] == used <= summary["capacity_bytes"] == 6 * 128 * 1024
    window = plans.pre_bins(name)
    assert summary["pre_window_bins"] == [window[0], window[-1]]


def test_club51_holds_the_address_bin_and_the_club_walk():
    summary = plans.check(plans.commands(PROFILE, "club51"))
    assert summary["bytes"] == 771_264 and round(summary["bytes"] / 1024) == 753
    assert summary["pre_ms"] == pytest.approx(51.0)
    assert plans.pre_bins("club51") == range(16, 48)
    assert plans.pre_bins("default") == range(20, 73)


@pytest.mark.parametrize(
    "line,reason",
    [
        ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8", "11 values"),
        ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 256", "uint8"),
        ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 -1", "uint8"),
        ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 x", "uint8"),
        ("phaseCaptureCfg 20 0 9 32 53 7 47 53 47 8 1", "window"),
        ("phaseCaptureCfg 20 53 0 32 53 7 47 53 47 8 1", "window"),
        ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 17", "window"),
        ("phaseCaptureCfg 76 53 9 32 53 7 47 53 47 8 1", "128-bin"),
        ("phaseCaptureCfg 20 53 9 76 53 7 47 53 47 8 1", "128-bin"),
        ("phaseCaptureCfg 20 53 9 32 53 7 76 53 47 8 1", "128-bin"),
        ("phaseCaptureCfg 20 53 9 32 53 7 47 53 76 8 1", "128-bin"),
        ("phaseCaptureCfg 20 8 30 32 8 20 47 8 47 15 1", "64 frames"),
        ("phaseCaptureCfg 20 8 1 32 8 30 47 8 47 34 1", "64 frames"),
        ("phaseCaptureCfg 16 32 18 32 53 7 47 53 47 8 1", "L3"),
        ("phaseCaptureCfg 0 128 1 0 128 7 0 128 0 4 1", "L3"),
    ],
)
def test_plans_outside_the_firmware_rules_are_refused(line, reason):
    lines = tuple(line if row.startswith("phaseCaptureCfg ") else row for row in PROFILE)
    with pytest.raises(ValueError, match=reason):
        plans.check(lines)


def test_iq8_is_not_a_reviewed_shot_format():
    lines = tuple(
        "captureFormat iq8" if row.startswith("captureFormat") else row for row in PROFILE
    )
    with pytest.raises(ValueError, match="iq16"):
        plans.check(lines)


def test_the_record_names_the_plan_and_every_line_sent():
    record = plans.record("club51", plans.commands(PROFILE, "club51"))
    assert record["name"] == "club51"
    assert record["commands"] == list(plans.commands(PROFILE, "club51"))
    assert record["phase_capture"]["pre"] == {"start": 16, "bins": 32, "frames": 17}
    assert record["trigger_bins"] == [16, 47]
    assert record["physical_timing_qualified"] is False
