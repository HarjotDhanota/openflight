"""The two-round IWR6843 transfer: wire formats, the simulated radar and the host's rounds.

The dumps are synthetic (``iwr6843_partial_scene``): the production 3-TX x 12-loop x
4-RX x 53-bin layout with clutter, noise and a moving target. Known-answer checks
compute the same quantities directly from the dump.
"""

from __future__ import annotations

import zlib

import numpy as np
import pytest

from openflight.iwr6843 import tracking, two_round
from openflight.iwr6843.dump import parse_dump, project_tx_pair
from openflight.iwr6843.shot import prepare_shot_dump
from openflight.iwr6843.sparse_capture import SparseDump
from openflight.iwr6843.two_round import (
    CELLS_HEADER,
    CELLS_MAGIC,
    FLAG_PREVIOUS_SESSION_TIMED_OUT,
    OVERVIEW_HEADER,
    OVERVIEW_MAGIC,
    REQUEST_HEADER,
    TwoRoundCapture,
    TwoRoundClient,
    TwoRoundError,
    TwoRoundRemoteError,
    TwoRoundTransferError,
    build_cells,
    build_overview,
    decode_request,
    encode_request,
    parse_cells,
    parse_overview,
    run_two_round,
)
from openflight.iwr6843.two_round_file import pack_sparse_capture, read_sparse_capture
from openflight.iwr6843.two_round_sim import SimulatedIWR6843TwoRound, SimulatedSerial
from tests.iwr6843_partial_scene import BINS, LOOPS, N_RX, pack, scene
from tests.test_iwr6843_dump_evidence import _evidence

N_FRAMES = 6
SESSION = 0x1234ABCD


@pytest.fixture(name="raw", scope="module")
def _raw() -> bytes:
    return pack(scene(N_FRAMES, seed=5))


@pytest.fixture(name="evidence_raw", scope="module")
def _evidence_raw() -> bytes:
    capture, frames, trailer = _evidence(N_FRAMES, 2)
    return pack(
        scene(N_FRAMES, seed=6),
        version=9,
        capture_evidence=capture,
        frame_evidence=frames,
        dump_trailer=trailer,
    )


def _counts(raw):
    return parse_dump(raw)[0]["range_bin_counts"]


def _mask(cells):
    mask = np.zeros((N_FRAMES, BINS), bool)
    for frame, local in cells:
        mask[frame, local] = True
    return mask


# --------------------------------------------------------------------------- the contract


def test_magics_and_versions_are_the_agreed_ones():
    assert two_round.OVERVIEW_MAGIC == b"ILO1" and two_round.OVERVIEW_VERSION == 1
    assert two_round.CELLS_MAGIC == b"ILC1" and two_round.CELLS_VERSION == 1
    taken = {b"ILD1", b"ILE1", b"ILT1", b"OFC2"}
    assert not {two_round.OVERVIEW_MAGIC, two_round.CELLS_MAGIC, two_round.REQUEST_MAGIC} & taken
    assert (OVERVIEW_HEADER.size, REQUEST_HEADER.size, CELLS_HEADER.size) == (44, 12, 28)


# --------------------------------------------------------------------------- overview


def test_overview_round_trip_carries_the_dump_header_sums_maps_and_noise(raw):
    data = build_overview(raw, session_id=SESSION)
    overview = parse_overview(data, session_id=SESSION)
    meta, cube = parse_dump(raw)
    assert overview.session_id == SESSION and overview.nbytes == len(data)
    assert overview.meta["range_bin_starts"] == meta["range_bin_starts"]
    assert overview.meta["frame_time_offsets_us"] == meta["frame_time_offsets_us"]
    # exact integer sums of TX1/TX3 per absolute bin and RX
    tdm = cube.reshape(N_FRAMES, LOOPS, 3, N_RX, BINS)
    expected = np.zeros(overview.sums.shape[:3], complex)
    for frame, start in enumerate(meta["range_bin_starts"]):
        expected[start : start + BINS] += tdm[frame][:, (0, 2)].sum(axis=0).transpose(2, 0, 1)
    np.testing.assert_array_equal(overview.sums[..., 0], expected.real)
    np.testing.assert_array_equal(overview.sums[..., 1], expected.imag)
    # the map is the host's own per-loop power to within the 1/500 dB code
    vertical = prepare_shot_dump(project_tx_pair(raw, (0, 2)))
    for scope in ("burst", "window"):
        host = tracking.loop_power(vertical.mti(scope))
        ratio = overview.power_maps[scope] / np.maximum(host, 1.0)
        assert np.all(np.abs(10 * np.log10(ratio)) <= 0.001 + 1e-9)
        assert overview.noise_power[scope] == pytest.approx(vertical.noise_power(scope), rel=1e-6)


def test_overview_carries_the_capture_evidence_and_trailer(evidence_raw):
    overview = parse_overview(build_overview(evidence_raw, session_id=SESSION))
    capture = TwoRoundCapture(overview)
    capture.add(*_all_cells(evidence_raw, overview))
    rebuilt_meta, _cube = parse_dump(capture.sparse_dump())
    meta, _cube = parse_dump(evidence_raw)
    for key in ("capture_evidence", "frame_evidence", "dump_trailer", "temperature_report"):
        assert rebuilt_meta[key] == meta[key]


def test_overview_corruption_and_truncation_are_detected(raw):
    data = build_overview(raw, session_id=SESSION)
    flipped = bytearray(data)
    flipped[len(data) // 2] ^= 0x01
    with pytest.raises(TwoRoundError, match="CRC"):
        parse_overview(bytes(flipped))
    with pytest.raises(TwoRoundError, match="truncated"):
        parse_overview(data[:-100])
    with pytest.raises(TwoRoundError, match="truncated"):
        parse_overview(data[:20])
    with pytest.raises(TwoRoundError, match="past its declared end"):
        parse_overview(data + b"\0")
    with pytest.raises(TwoRoundError, match="magic"):
        parse_overview(b"ILD1" + data[4:])
    with pytest.raises(TwoRoundError, match="session"):
        parse_overview(data, session_id=SESSION + 1)


def test_overview_needs_a_three_tx_variable_iq16_dump(raw):
    with pytest.raises(TwoRoundError, match="3-TX"):
        build_overview(project_tx_pair(raw, (0, 2)), session_id=SESSION)


# --------------------------------------------------------------------------- request


def test_request_round_trip_as_bin_runs(raw):
    counts = _counts(raw)
    rng = np.random.default_rng(1)
    mask = rng.random((N_FRAMES, BINS)) < 0.2
    request = encode_request(mask, session_id=SESSION, sequence=2, range_bin_counts=counts)
    back = decode_request(request.data, range_bin_counts=counts, n_samples=BINS)
    np.testing.assert_array_equal(back.mask, mask)
    assert (back.session_id, back.sequence, back.crc) == (SESSION, 2, request.crc)
    runs = sum(len(two_round._runs(row)) for row in mask)  # pylint: disable=protected-access
    assert len(request.data) == REQUEST_HEADER.size + N_FRAMES + 2 * runs + 4
    assert request.command().startswith(b"cells ") and request.command().endswith(b"\n")


def test_request_encoding_is_runs_of_bins():
    mask = _mask([(0, 3), (0, 4), (0, 5), (0, 9), (2, 0), (5, 52)])
    request = encode_request(mask, session_id=7, sequence=1, range_bin_counts=(BINS,) * N_FRAMES)
    body = request.data[REQUEST_HEADER.size : -4]
    assert body == bytes([2, 3, 3, 9, 1, 0, 1, 0, 1, 0, 0, 1, 52, 1])


def test_request_corruption_truncation_and_bad_runs_are_refused(raw):
    counts = _counts(raw)
    request = encode_request(
        _mask([(1, 4), (1, 5)]), session_id=SESSION, sequence=1, range_bin_counts=counts
    )
    data = bytearray(request.data)
    data[REQUEST_HEADER.size + 2] ^= 0x10
    with pytest.raises(TwoRoundError, match="CRC"):
        decode_request(bytes(data), range_bin_counts=counts, n_samples=BINS)
    with pytest.raises(TwoRoundError):
        decode_request(request.data[:-6], range_bin_counts=counts, n_samples=BINS)

    def sealed(body: bytes) -> bytes:
        return body + zlib.crc32(body).to_bytes(4, "little")

    header = REQUEST_HEADER.pack(b"ILQ1", 1, 1, N_FRAMES, SESSION)
    beyond = sealed(header + bytes([1, 50, 4]) + bytes(N_FRAMES - 1))
    with pytest.raises(TwoRoundError, match="bad run"):
        decode_request(beyond, range_bin_counts=counts, n_samples=BINS)
    overlap = sealed(header + bytes([2, 3, 4, 5, 2]) + bytes(N_FRAMES - 1))
    with pytest.raises(TwoRoundError, match="bad run"):
        decode_request(overlap, range_bin_counts=counts, n_samples=BINS)
    with pytest.raises(TwoRoundError, match="beyond"):
        mask = np.zeros((N_FRAMES, BINS + 2), bool)
        mask[0, BINS + 1] = True
        encode_request(mask, session_id=1, sequence=1, range_bin_counts=counts)


# --------------------------------------------------------------------------- cells


def _all_cells(raw, overview):
    mask = np.ones((N_FRAMES, BINS), bool)
    request = encode_request(
        mask, session_id=overview.session_id, sequence=1, range_bin_counts=_counts(raw)
    )
    return request, parse_cells(build_cells(raw, request), request, overview.meta)


def test_cells_round_trip_bit_exact(raw):
    meta, cube = parse_dump(raw)
    request = encode_request(
        _mask([(0, 2), (3, 40), (3, 41)]),
        session_id=SESSION,
        sequence=1,
        range_bin_counts=_counts(raw),
    )
    cells = parse_cells(build_cells(raw, request), request, meta)
    assert cells.shape == (3, 3 * LOOPS, N_RX, 2)
    np.testing.assert_array_equal(cells[1, ..., 1], cube[3, :, :, 40].real)
    np.testing.assert_array_equal(cells[1, ..., 0], cube[3, :, :, 40].imag)


def test_cells_for_another_request_or_session_are_refused(raw):
    meta, _cube = parse_dump(raw)
    counts = _counts(raw)
    first = encode_request(_mask([(0, 2)]), session_id=SESSION, sequence=1, range_bin_counts=counts)
    second = encode_request(
        _mask([(0, 2)]), session_id=SESSION, sequence=2, range_bin_counts=counts
    )
    other = encode_request(
        _mask([(0, 2)]), session_id=SESSION + 1, sequence=1, range_bin_counts=counts
    )
    late = build_cells(raw, first)
    with pytest.raises(TwoRoundError, match="does not answer"):
        parse_cells(late, second, meta)
    with pytest.raises(TwoRoundError, match="does not answer"):
        parse_cells(late, other, meta)
    flipped = bytearray(late)
    flipped[CELLS_HEADER.size + 1] ^= 0x01
    with pytest.raises(TwoRoundError, match="CRC"):
        parse_cells(bytes(flipped), first, meta)
    with pytest.raises(TwoRoundError, match="truncated"):
        parse_cells(late[:-10], first, meta)


# --------------------------------------------------------------------------- rebuild


def test_every_cell_sent_rebuilds_the_dump_byte_for_byte(raw, evidence_raw):
    for dump in (raw, evidence_raw):
        overview = parse_overview(build_overview(dump, session_id=SESSION))
        capture = TwoRoundCapture(overview)
        capture.add(*_all_cells(dump, overview))
        assert bytes(capture.sparse_dump()) == dump


def test_fills_keep_every_window_scope_mean(raw):
    overview = parse_overview(build_overview(raw, session_id=SESSION))
    capture = TwoRoundCapture(overview)
    sent = _mask([(f, b) for f in range(N_FRAMES) for b in range(10, 16)])
    request = encode_request(sent, session_id=SESSION, sequence=1, range_bin_counts=_counts(raw))
    capture.add(request, parse_cells(build_cells(raw, request), request, overview.meta))
    sparse = capture.sparse_dump()
    assert isinstance(sparse, SparseDump)
    full = prepare_shot_dump(project_tx_pair(raw, (0, 2)))
    part = prepare_shot_dump(project_tx_pair(sparse, (0, 2)))
    full_mti, part_mti = np.asarray(full.mti("window")), np.asarray(part.mti("window"))
    np.testing.assert_array_equal(part_mti[:, ..., 10:16], full_mti[:, ..., 10:16])
    np.testing.assert_array_equal(
        np.asarray(part.mti("burst"))[..., 10:16], np.asarray(full.mti("burst"))[..., 10:16]
    )


# --------------------------------------------------------------------------- simulated radar


def test_simulated_radar_serves_several_requests_on_one_frozen_capture(raw):
    radar = SimulatedIWR6843TwoRound()
    session = radar.freeze(raw)
    client = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)
    overview, _data = client.overview()
    assert overview.session_id == session
    meta, cube = parse_dump(raw)
    capture = TwoRoundCapture(overview)
    for sequence, cells in enumerate(([(0, 1), (2, 7)], [(5, 52)], [(0, 1)]), start=1):
        request = encode_request(
            _mask(cells), session_id=session, sequence=sequence, range_bin_counts=_counts(raw)
        )
        samples, _ = client.cells(request, meta)
        capture.add(request, samples)
        frame, local = cells[-1]
        np.testing.assert_array_equal(samples[-1, ..., 1], cube[frame, :, :, local].real)
    assert radar.freeze(raw) is None and radar.ignored_triggers == 1
    client.release(session)
    assert not radar.frozen and radar.recoveries == []


def test_stale_sessions_and_interleaved_commands_are_refused(raw):
    clock = [0.0]
    radar = SimulatedIWR6843TwoRound(clock=lambda: clock[0], first_session_id=40)
    session = radar.freeze(raw)
    client = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)
    overview, _ = client.overview()
    stale = encode_request(
        _mask([(0, 1)]), session_id=session - 1, sequence=1, range_bin_counts=_counts(raw)
    )
    with pytest.raises(TwoRoundRemoteError, match="stale session"):
        client.cells(stale, overview.meta)
    with pytest.raises(TwoRoundRemoteError, match="stale session"):
        client.release(session + 1)
    for other in (b"clockProbe\n", b"cp\n", b"sensorStart\n"):
        assert radar.command(other).startswith(b"Error: session")
    assert radar.frozen
    # a reconnecting host asks for the overview again and gets the same session
    again, _ = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05).overview()
    assert again.session_id == session
    client.release(session)
    assert radar.command(b"clockProbe\n").endswith(b"Done\n" + two_round.CLI_PROMPT)
    with pytest.raises(TwoRoundRemoteError, match="no frozen capture"):
        client.overview()


def test_hold_timeout_rearms_logged_and_flags_the_next_session(raw):
    clock = [0.0]
    radar = SimulatedIWR6843TwoRound(clock=lambda: clock[0], hold_timeout_s=30.0)
    session = radar.freeze(raw)
    client = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)
    overview, _ = client.overview()
    assert overview.flags == 0
    clock[0] = 31.0
    request = encode_request(
        _mask([(0, 1)]), session_id=session, sequence=1, range_bin_counts=_counts(raw)
    )
    with pytest.raises(TwoRoundRemoteError, match="no frozen capture"):
        client.cells(request, overview.meta)
    assert [(e.session_id, e.reason) for e in radar.recoveries] == [(session, "hold_timeout")]
    radar.freeze(raw)
    flagged, _ = client.overview()
    assert flagged.flags & FLAG_PREVIOUS_SESSION_TIMED_OUT and flagged.session_id == session + 1


def test_client_reads_through_an_echo_and_refuses_damaged_replies(raw):
    radar = SimulatedIWR6843TwoRound(echo=True)
    session = radar.freeze(raw)
    serial = SimulatedSerial(radar)
    client = TwoRoundClient(serial, timeout_s=0.05)
    overview, data = client.overview()
    assert data.startswith(OVERVIEW_MAGIC) and overview.session_id == session
    radar.corrupt_next = 1
    with pytest.raises(TwoRoundError, match="CRC"):
        client.overview()
    radar.truncate_next = 1000
    with pytest.raises(TwoRoundError):
        client.overview()
    serial.read(1 << 20)  # drain what the truncated reply left on the line
    request = encode_request(
        _mask([(1, 1)]), session_id=session, sequence=1, range_bin_counts=_counts(raw)
    )
    cells, data = client.cells(request, overview.meta)
    assert data.startswith(CELLS_MAGIC) and cells.shape[0] == 1


# --------------------------------------------------------------------------- host rounds


def test_rounds_ask_for_what_the_analysis_read_until_nothing_is_missing(raw):
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(raw)
    client = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)
    _meta, cube = parse_dump(raw)

    def analyse(dump):
        _m, sparse = parse_dump(dump)
        first = sparse[1, 0, 0, 5]
        # the second read depends on the first: only round 2's real value names it
        second_bin = 6 if first == cube[1, 0, 0, 5] else 30
        return sparse[2, 0, 0, second_bin]

    result, transfer = run_two_round(client, analyse)
    assert result == cube[2, 0, 0, 6]
    assert transfer.cells_per_round[0] >= 2 and transfer.cell_rounds >= 2
    assert transfer.released and not radar.frozen
    assert transfer.overview_bytes == len(build_overview(raw, session_id=transfer.session_id))
    assert transfer.link_bytes > transfer.overview_bytes


def test_rounds_end_with_every_cell_when_reads_never_close(raw):
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(raw)
    client = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)
    reads = iter(range(10_000))

    def analyse(dump):
        _m, sparse = parse_dump(dump)
        index = next(reads)
        return sparse[index % N_FRAMES, 0, 0, index % BINS]

    _result, transfer = run_two_round(client, analyse, max_cell_rounds=2)
    assert transfer.complete_request
    assert sum(transfer.cells_per_round) == N_FRAMES * BINS


def test_a_failed_round_keeps_what_arrived_and_releases_explicitly(raw):
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(raw)
    client = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)

    def analyse(dump):
        radar.corrupt_next = 4  # the request, its retry, the complete request and its retry
        _m, sparse = parse_dump(dump)
        return sparse[0, 0, 0, 0]

    with pytest.raises(TwoRoundTransferError) as caught:
        run_two_round(client, analyse)
    assert caught.value.capture is not None
    assert caught.value.transfer.overview_bytes > 0
    assert "CRC" in caught.value.transfer.error
    assert caught.value.transfer.released and not radar.frozen


def test_a_damaged_overview_is_asked_for_again(raw):
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(raw)
    radar.corrupt_next = 1
    client = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)
    _meta, cube = parse_dump(raw)

    def analyse(dump):
        _m, sparse = parse_dump(dump)
        return sparse[3, 0, 0, 9]

    result, transfer = run_two_round(client, analyse)
    assert result == cube[3, 0, 0, 9]
    assert transfer.released and transfer.error is None
    assert radar.commands.count("overview") == 2


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("frame_count", [6, 24])
def test_fragmented_requests_fit_the_ti_cli_and_keep_exact_samples(raw, complete, frame_count):
    class LimitedSerial(SimulatedSerial):
        def write(self, data):
            assert len(data) <= 255, f"TI CLI line exceeds its 255-byte read: {len(data)}"
            return super().write(data)

    if frame_count != N_FRAMES:
        raw = pack(scene(frame_count, seed=5))
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(raw)
    client = TwoRoundClient(LimitedSerial(radar), timeout_s=0.05)
    _meta, cube = parse_dump(raw)

    def analyse(dump):
        _m, sparse = parse_dump(dump)
        return np.array(
            [
                [sparse[frame, 0, 0, local] for local in range(0, BINS, 2)]
                for frame in range(frame_count)
            ]
        )

    result, transfer = run_two_round(client, analyse, max_cell_rounds=0 if complete else 2)
    np.testing.assert_array_equal(result, cube[:, 0, 0, ::2])
    assert transfer.complete_request is complete
    requests = [data for kind, data in transfer.payloads if kind == "request"]
    decoded = [
        decode_request(data, range_bin_counts=_counts(raw), n_samples=BINS) for data in requests
    ]
    assert [request.sequence for request in decoded] == list(range(1, len(decoded) + 1))
    assert all(len(request.command()) <= 255 for request in decoded)
    if not complete:
        assert len(decoded) > 1
    assert transfer.released and not radar.frozen
    _header, replay = read_sparse_capture(pack_sparse_capture(transfer))
    np.testing.assert_array_equal(analyse(replay.sparse_dump()), result)


@pytest.mark.parametrize("error_type", [ValueError, OSError])
def test_analysis_failure_preserves_received_cells_and_releases_the_ring(raw, error_type):
    radar = SimulatedIWR6843TwoRound()
    radar.freeze(raw)
    client = TwoRoundClient(SimulatedSerial(radar), timeout_s=0.05)
    calls = 0

    def analyse(dump):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise error_type("analysis failed")
        return parse_dump(dump)[1][0, 0, 0, 0]

    with pytest.raises(TwoRoundTransferError) as caught:
        run_two_round(client, analyse)
    assert isinstance(caught.value.__cause__, error_type)
    assert any(kind == "cells" for kind, _ in caught.value.transfer.payloads)
    assert caught.value.transfer.released and not radar.frozen


def test_lost_release_link_is_reported_without_discarding_the_capture(raw):
    class LostRelease(TwoRoundClient):
        def release(self, session_id):
            raise OSError("link disconnected")

    radar = SimulatedIWR6843TwoRound()
    radar.freeze(raw)
    client = LostRelease(SimulatedSerial(radar), timeout_s=0.05)
    result, transfer = run_two_round(client, lambda dump: parse_dump(dump)[1][0, 0, 0, 0])
    assert result == parse_dump(raw)[1][0, 0, 0, 0]
    assert not transfer.released and radar.frozen
    assert "release failed: link disconnected" in transfer.error
    assert any(kind == "cells" for kind, _ in transfer.payloads)
