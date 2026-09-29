"""The setup-capture radar profiles fit the firmware and the static range gate."""

from pathlib import Path

import pytest

from openflight.iwr6843 import range_evidence

CONFIG = Path(__file__).resolve().parents[1] / "config"
L3_BYTES = 786_432
IQ16_BYTES = 4
N_RX = 4


def _commands(path: Path) -> dict[str, list[list[str]]]:
    commands: dict[str, list[list[str]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("%"):
            name, *values = line.split()
            commands.setdefault(name, []).append(values)
    return commands


def _ring(path: Path) -> tuple[int, int]:
    """(frames, bytes) the firmware keeps for this profile."""
    commands = _commands(path)
    n_tx = len(commands["chirpCfg"])
    loops = int(commands["frameCfg"][0][2])
    phase = [int(value) for value in commands["phaseCaptureCfg"][0]]
    pre_bins, pre_frames = phase[1], phase[2]
    impact_bins, impact_frames = phase[4], phase[5]
    post_bins, ball_frames = phase[7], phase[9]
    per_bin = n_tx * loops * N_RX * IQ16_BYTES
    ring = per_bin * (pre_bins * pre_frames + impact_bins * impact_frames + post_bins * ball_frames)
    return pre_frames + impact_frames + ball_frames, ring


@pytest.mark.parametrize(
    "name",
    [
        "iwr6843_static_range_24f3ms_53bin_iq16.cfg",
        "iwr6843_static_range_14f3ms_53bin_iq16.cfg",
    ],
)
def test_a_setup_profile_fits_l3_and_the_static_gate(name):
    frames, ring = _ring(CONFIG / name)

    assert frames >= range_evidence._STATIC_V2_MIN_FRAME_COUNT
    assert ring <= L3_BYTES


def test_the_short_profile_changes_only_the_frame_counts():
    full = _commands(CONFIG / "iwr6843_static_range_24f3ms_53bin_iq16.cfg")
    short = _commands(CONFIG / "iwr6843_static_range_14f3ms_53bin_iq16.cfg")

    assert {key: value for key, value in full.items() if key != "phaseCaptureCfg"} == {
        key: value for key, value in short.items() if key != "phaseCaptureCfg"
    }
    full_phase = full["phaseCaptureCfg"][0]
    short_phase = short["phaseCaptureCfg"][0]
    # same windows (starts and widths); only frame counts differ
    for index in (0, 1, 3, 4, 6, 7, 8, 10):
        assert full_phase[index] == short_phase[index]
    assert _ring(CONFIG / "iwr6843_static_range_14f3ms_53bin_iq16.cfg")[0] == 14
