"""The clutter map extension of the IWR6843 dump (version 10).

The board's clutter map (the tee band's noise map, l3_band.h: mean, spread
and history per bin, learned on frames with no club track) as it stood when
the capture was dumped. A capture holds 24 frames, a median of two of them
idle before the club appears, so a replay cannot learn the map from the
capture itself (2026-10-01); it starts from this snapshot instead. Layout
(firmware/iwr6843/dump_format.h): the v7 header and temperature report, then
u16 first bin, u16 count, u32 updates, count float32 means, count float32
spreads, count u8 update counts.
"""

from __future__ import annotations

import ctypes

import numpy as np
import pytest

from openflight.iwr6843 import dump as d, firmware_host as fw

TEMPERATURE = {key: 40 for key in d.TEMP_REPORT_KEYS}


def clutter_map(first_bin=20, count=53):
    return {
        "first_bin": first_bin,
        "count": count,
        "updates": 900,
        "avg": tuple(float(1000 + i) for i in range(count)),
        "dev": tuple(float(10 + i) / 4 for i in range(count)),
        "seen": tuple(min(255, 200 + i) for i in range(count)),
    }


def timed_cube(n_frames=3, width=4):
    cube = np.zeros((n_frames, 6, 4, width), dtype=np.complex128)
    cube[..., 1] = 100 + 50j
    return cube


def pack(version=d.DUMP_VERSION_CLUTTER, **overrides):
    n_frames = 3
    kwargs = dict(
        n_tx=3,
        version=version,
        sample_fmt=d.SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        range_bin_starts=(20,) * n_frames,
        range_bin_counts=(4,) * n_frames,
        frame_time_offsets_us=(0, 3000, 6000),
        temperature_report=TEMPERATURE,
        clutter_map=clutter_map(),
    )
    kwargs.update(overrides)
    return d.pack_dump(timed_cube(n_frames), **kwargs)


def test_the_clutter_map_round_trips_through_the_dump():
    raw = pack()
    meta, cube = d.parse_dump(raw)
    got = meta["clutter_map"]
    want = clutter_map()
    assert (got["first_bin"], got["count"], got["updates"]) == (20, 53, 900)
    assert got["avg"] == pytest.approx(want["avg"])
    assert got["dev"] == pytest.approx(want["dev"])
    assert got["seen"] == want["seen"]
    assert meta["temperature_report"] == TEMPERATURE
    assert np.allclose(cube[..., 1], 100 + 50j)


def test_the_header_accounts_for_the_extension():
    raw = pack()
    meta = d.parse_header(raw)
    extension = d.CLUTTER_REPORT.size + 53 * (4 + 4 + 1)
    assert meta["header_nbytes"] == d.HEADER.size + d.TEMP_REPORT.size + extension
    assert len(raw) == meta["header_nbytes"] + d.payload_nbytes(meta, raw)


def test_a_version_10_dump_needs_a_map_and_older_ones_refuse_one():
    with pytest.raises(ValueError, match="clutter map"):
        pack(clutter_map=None)
    with pytest.raises(ValueError, match="clutter map"):
        pack(version=7)


def test_a_map_wider_than_the_board_keeps_is_refused():
    with pytest.raises(ValueError, match="clutter map"):
        pack(clutter_map=clutter_map(count=fw.BAND_NOISE_BINS + 1))


def test_a_short_extension_waits_for_more_bytes():
    """The Pi reads the dump as it arrives: a partial map is a ValueError, which
    its reader treats as "not yet" (driver.py, monitor.py)."""
    raw = pack()
    cut = d.HEADER.size + d.TEMP_REPORT.size + d.CLUTTER_REPORT.size + 10
    with pytest.raises(ValueError, match="short clutter map"):
        d.parse_header(raw[:cut])


def test_an_empty_map_is_allowed_before_the_board_has_learned_anything():
    raw = pack(clutter_map=clutter_map(count=0))
    assert d.parse_header(raw)["clutter_map"]["count"] == 0


def test_the_map_loads_into_the_boards_noise_map_struct():
    noise = d.clutter_map_struct(clutter_map())
    assert isinstance(noise, fw.BandNoise)
    assert (noise.firstBin, noise.count, noise.updates) == (20, 53, 900)
    assert noise.avg[52] == pytest.approx(1052.0)
    assert noise.dev[4] == pytest.approx(3.5)
    assert noise.seen[0] == 200
    assert ctypes.sizeof(noise) == ctypes.sizeof(fw.BandNoise)


def test_rewriting_a_dump_keeps_its_map():
    raw = pack()
    again = d.select_tdm_loops(raw, start=0, count=1)
    assert d.parse_header(again)["clutter_map"]["updates"] == 900


# --- the replay starts from the dump's map -------------------------------------


from openflight.iwr6843 import firmware_replay as fr  # noqa: E402

needs_compiler = pytest.mark.skipif(
    fw.host_compiler() is None, reason="no C compiler for the firmware modules"
)
GOLFER_DUMP = fr.RECORDINGS_DIR / "golfer_2026-09" / "iwr6843_20260919_190111_221_017.l3dump"


def with_map(raw: bytes, clutter: dict) -> bytes:
    """A recorded v7 capture re-packed as v10 carrying ``clutter``."""
    meta, cube = d.parse_dump(raw)
    return d.pack_dump(
        cube,
        n_tx=meta["n_tx"],
        trigger_frame=meta["trigger_frame"],
        version=d.DUMP_VERSION_CLUTTER,
        frame_period_us=meta["frame_period_us"],
        sample_fmt=meta["sample_fmt"],
        range_bin_starts=meta["range_bin_starts"],
        range_bin_counts=meta["range_bin_counts"],
        frame_time_offsets_us=meta["frame_time_offsets_us"],
        temperature_report=meta["temperature_report"],
        clutter_map=clutter,
    )


@needs_compiler
@pytest.mark.parametrize("map_count", [None, 0, 4])
@pytest.mark.parametrize("band_bins", [None, 2.0])
def test_replay_reports_whether_background_history_was_recorded(map_count, band_bins):
    """A recorded map, including an empty one, is distinct from inferred history."""
    raw = (
        pack(version=7, clutter_map=None)
        if map_count is None
        else pack(clutter_map=clutter_map(count=map_count))
    )
    result = fr.replay_dump(raw, fr.ReplayConfig(tee_bin=22, band_bins=band_bins))
    assert result.clutter_map_recorded is (map_count is not None)
    report = fr.format_report(result)
    if band_bins is None:
        assert "replay background:" not in report
    elif map_count is None:
        assert "replay background: no recorded clutter map; initialized from capture only" in report
    else:
        assert "replay background: recorded clutter map; training not qualified" in report


def saturated_map(raw: bytes) -> dict:
    """A map that calls everything in the capture's window clutter."""
    meta = d.parse_header(raw)
    d._parse_frame_metadata(raw, meta)  # pylint: disable=protected-access
    first, count = meta["range_bin_starts"][0], min(meta["range_bin_counts"][0], fw.BAND_NOISE_BINS)
    return {
        "first_bin": first,
        "count": count,
        "updates": 1000,
        "avg": (1.0e15,) * count,
        "dev": (0.0,) * count,
        "seen": (255,) * count,
    }


def kiosk(sigmas: float) -> fr.ReplayConfig:
    return fr.ReplayConfig(
        tee_bin=52, band_bins=6.0, snr=1.0, overrides={"fit.clutterSigmas": sigmas}
    )


@needs_compiler
@pytest.mark.skipif(not GOLFER_DUMP.exists(), reason="recording missing")
def test_the_replay_filters_against_the_dumps_map():
    raw = GOLFER_DUMP.read_bytes()
    plain = fr.replay_dump(raw, kiosk(3.0))
    assert plain.fired_frame is not None and any(
        p.frame <= plain.fired_frame for p in plain.points
    ), "the capture tracks a club's approach without a map"
    seeded = fr.replay_dump(with_map(raw, saturated_map(raw)), kiosk(3.0))
    # The map holds while the capture's window does: a new window restarts it
    # (l3_band_noise_update_span), on the board as here.
    meta = d.parse_header(raw)
    d._parse_frame_metadata(raw, meta)  # pylint: disable=protected-access
    starts = meta["range_bin_starts"]
    held = next(f for f, start in enumerate(starts) if start != starts[0])
    assert held > plain.fired_frame, "the unfiltered club fires inside the map's window"
    assert [p for p in seeded.points if p.frame < held] == []
    assert seeded.fired_frame is None or seeded.fired_frame >= held


@needs_compiler
@pytest.mark.skipif(not GOLFER_DUMP.exists(), reason="recording missing")
def test_a_map_with_no_history_replays_as_no_map():
    """The map also places the tee band (l3_band_place), as on the board, so a
    learned map changes the replay even with the filter off; one that has
    learned nothing must not."""
    raw = GOLFER_DUMP.read_bytes()
    empty = {**saturated_map(raw), "updates": 0}
    empty["seen"] = (0,) * empty["count"]
    plain = fr.replay_dump(raw, kiosk(3.0))
    seeded = fr.replay_dump(with_map(raw, empty), kiosk(3.0))
    assert seeded.points == plain.points
    assert seeded.fired_frame == plain.fired_frame


# --- which range window the capture was recorded with --------------------------


@pytest.mark.parametrize("window", ["none", "hann"])
def test_the_range_window_round_trips(window):
    raw = pack(range_window=window)
    assert d.parse_header(raw)["range_window"] == window


def test_a_version_10_dump_says_none_unless_told():
    assert d.parse_header(pack())["range_window"] == "none"


def test_an_unknown_window_is_refused_both_ways():
    with pytest.raises(ValueError, match="range window"):
        pack(range_window="blackman")
    raw = bytearray(pack(range_window="hann"))
    offset = d.HEADER.size + d.TEMP_REPORT.size + 8  # first bin, count, updates
    raw[offset] = 9
    with pytest.raises(ValueError, match="range window"):
        d.parse_header(bytes(raw))


def test_a_window_needs_version_10():
    with pytest.raises(ValueError, match="range window"):
        pack(version=7, clutter_map=None, range_window="hann")
