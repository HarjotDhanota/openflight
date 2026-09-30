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
        "iwr6843_static_range_18f3ms_72bin_iq16.cfg",
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


def _patch(camera, distance):
    from openflight.camera import tester_server as ts  # noqa: PLC0415
    from openflight.camera.ground_patch import patch_at  # noqa: PLC0415

    return ts.patch_record(
        camera,
        patch_at(camera, distance),
        tilt={"camera_pitch_deg": 0.0},
        roll_deg=0.0,
        source="tester_dragged",
    )


def test_a_far_patch_takes_the_wider_setup_profile():
    """P8-1/P8-3: bins 8-60 end at 2.81 m apparent; a patch past about 2.3 m outgrows them."""
    from openflight.camera import tester_server as ts  # noqa: PLC0415
    from openflight.camera.reference_ball_range import BallPlaneCamera  # noqa: PLC0415

    camera = BallPlaneCamera.nominal(
        focal_px=933.3334,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=0.0,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.0014, 0.0588),
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
    )
    default = CONFIG / "iwr6843_static_range_24f3ms_53bin_iq16.cfg"
    far = CONFIG / "iwr6843_static_range_18f3ms_72bin_iq16.cfg"
    bias = 0.0659

    near_low, near_high = ts.static_config_reach_m(default)
    far_low, far_high = ts.static_config_reach_m(far)
    assert (near_low, near_high) == pytest.approx((0.375, 2.8125))
    assert (far_low, far_high) == pytest.approx((0.375, 3.703125))

    chosen = {
        distance: ts.static_config_for_patch(_patch(camera, distance), default, far, bias)
        for distance in (1.25, 2.0, 2.5, 3.0)
    }
    assert {distance: facts["chosen"] for distance, (_path, facts) in chosen.items()} == {
        1.25: "default",
        2.0: "default",
        2.5: "far",
        3.0: "far",
    }
    for distance, (path, facts) in chosen.items():
        usable = facts["profiles"][facts["chosen"]]["usable_corrected_m"][1]
        record = _patch(camera, distance)
        # the patch's far edge and the window margin lie inside the chosen capture
        assert record["nominal_edges_m"]["far_slant_m"] + 0.1 <= usable
        assert path == (far if facts["chosen"] == "far" else default)
    # without a patch (an older setup), the default profile
    assert ts.static_config_for_patch(None, default, far, bias)[1]["chosen"] == "default"
