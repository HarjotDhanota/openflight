"""A partly sent capture (``sparse_capture``) runs through the production estimator seams.

A ``SparseDump`` parses to a cube that records the (frame, bin) cells read, its MTI
knows its scope, per-loop power comes from the overview map for unsent cells and the
noise medians from the overview scalars. A plain dump takes none of these paths.
"""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843 import doa, tracking
from openflight.iwr6843.dump import parse_dump, project_tx_pair
from openflight.iwr6843.shot import geometry_from_header, prepare_shot_dump
from openflight.iwr6843.sparse_capture import (
    SparseCube,
    SparseDump,
    SparseEvidence,
    noise_override,
)
from openflight.iwr6843.tracking import BallTrack
from tests.iwr6843_partial_scene import BINS, LOOPS, pack, scene

N_FRAMES = 6


def _raw() -> bytes:
    return pack(scene(N_FRAMES, seed=3))


def _evidence(sent=None) -> SparseEvidence:
    rows = N_FRAMES * LOOPS
    return SparseEvidence(
        range_bin_counts=(BINS,) * N_FRAMES,
        n_samples=BINS,
        sent=np.zeros((N_FRAMES, BINS), bool) if sent is None else sent,
        power_maps={
            "burst": np.full((rows, BINS), 7.0),
            "window": np.full((rows, BINS), 11.0),
        },
        noise_power={"burst": 3.5, "window": 4.5},
    )


def test_plain_dump_parses_to_a_plain_cube():
    _meta, cube = parse_dump(_raw())
    assert type(cube) is np.ndarray


def test_sparse_dump_records_cell_reads_but_not_slices():
    evidence = _evidence()
    _meta, cube = parse_dump(SparseDump(_raw(), evidence))
    assert isinstance(cube, SparseCube)
    _ = cube[2, 5, 1, 17]
    _ = cube[3, ..., 9]
    _ = cube[1, :, :, 4:8]
    _ = cube[4]
    assert evidence.reads == {(2, 17), (3, 9)}
    assert evidence.unsent_reads().sum() == 2


def test_projection_keeps_the_capture_evidence():
    evidence = _evidence()
    projected = project_tx_pair(SparseDump(_raw(), evidence), (0, 2))
    assert isinstance(projected, SparseDump)
    assert projected.evidence is evidence
    plain = project_tx_pair(_raw(), (0, 2))
    assert type(plain) is bytes
    assert bytes(projected) == plain


def test_reshape_that_moves_the_frame_axis_stops_recording():
    evidence = _evidence()
    _meta, cube = parse_dump(SparseDump(_raw(), evidence))
    flat = cube.reshape(-1, BINS)
    _ = flat[2, 3]
    assert evidence.reads == set()


@pytest.mark.parametrize("scope", ["burst", "window"])
def test_loop_power_takes_unsent_cells_from_the_map(scope):
    raw = project_tx_pair(_raw(), (0, 2))
    sent = np.zeros((N_FRAMES, BINS), bool)
    sent[2, 10:14] = True
    evidence = _evidence(sent)
    plain = prepare_shot_dump(raw)
    sparse = prepare_shot_dump(SparseDump(raw, evidence))
    full_power = tracking.loop_power(plain.mti(scope))
    power = tracking.loop_power(sparse.mti(scope))
    assert sparse.mti(scope).scope == scope
    rows = slice(2 * LOOPS, 3 * LOOPS)
    np.testing.assert_array_equal(power[rows, 10:14], full_power[rows, 10:14])
    expected_fill = 7.0 if scope == "burst" else 11.0
    assert np.all(power[rows, :10] == expected_fill)
    assert np.all(power[: 2 * LOOPS] == expected_fill)
    assert type(power) is np.ndarray


def test_noise_power_comes_from_the_overview_scalars():
    raw = project_tx_pair(_raw(), (0, 2))
    sparse = prepare_shot_dump(SparseDump(raw, _evidence()))
    assert sparse.noise_power("burst") == 3.5
    assert sparse.noise_power("window") == 4.5
    assert noise_override(sparse.mti("burst")) == 3.5
    plain = prepare_shot_dump(raw)
    assert noise_override(plain.mti("burst")) is None
    mti = plain.mti("burst")
    assert plain.noise_power("burst") == float(np.median(np.abs(mti) ** 2))


def _track(geo) -> BallTrack:
    return BallTrack(
        slope_bins=30.0,
        intercept_bins=40.0,
        n_inliers=20,
        rms_bins=0.2,
        speed_ms=30.0 * geo.range_res_m,
        t_first=0.0,
        t_last=geo.n_frames * geo.frame_period_s,
        low_confidence=False,
    )


def test_doa_noise_power_defaults_to_the_cube_median():
    raw = project_tx_pair(_raw(), (0, 2))
    meta, cube = parse_dump(raw)
    geo = geometry_from_header(meta)
    mti = tracking.mti_filter(cube, range_domain=True, geometry=geo)
    from openflight.iwr6843.calibration import Calibration

    cal = Calibration.identity()
    track = _track(geo)
    kwargs = {"snr_min": 0.0, "tdm_sign": 1}
    default = doa.snapshot_series(mti, track, geo, cal, **kwargs)
    median = float(np.median(np.abs(mti) ** 2))
    explicit = doa.snapshot_series(mti, track, geo, cal, noise_power=median, **kwargs)
    assert default and len(default) == len(explicit)
    for a, b in zip(default, explicit):
        assert a[0] == b[0] and a[3] == b[3]
        np.testing.assert_array_equal(a[2], b[2])
    halved = doa.snapshot_series(mti, track, geo, cal, noise_power=median / 2, **kwargs)
    assert [round(p[3] / q[3], 9) for p, q in zip(halved, default)] == [2.0] * len(default)
    points = doa.angle_points(mti, track, geo, cal, snr_min=0.0, tdm_sign=1)
    same = doa.angle_points(mti, track, geo, cal, snr_min=0.0, tdm_sign=1, noise_power=median)
    assert points == same
