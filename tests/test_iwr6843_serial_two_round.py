"""Real child-process protocol exchanges over a synthetic board byte stream."""

import functools
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.command_acceptance import CommandAcceptanceError
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.serial_process import IWR6843SerialProcess
from openflight.iwr6843.two_round import (
    TwoRoundError,
    build_cells,
    encode_request,
    parse_overview,
    run_two_round,
)
from tests.iwr6843_partial_scene import pack, scene

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def live_radar(tmp_path):
    raw = pack(scene(6))
    path = tmp_path / "synthetic.l3dump"
    path.write_bytes(raw)
    opened = []

    def open_radar(**options):
        events = []
        radar = IWR6843SerialProcess(
            events=events,
            radar_factory="tests.iwr6843_two_round_live_fake:Radar",
            factory_kwargs={"raw_path": str(path), **options},
            popen=functools.partial(subprocess.Popen, cwd=REPO),
        )
        opened.append(radar)
        return radar, events

    yield open_radar
    for radar in opened:
        radar.close()


def test_real_child_owns_frozen_session_and_returns_exact_cells(live_radar):
    radar, _events = live_radar()
    before = time.monotonic_ns()
    link = radar.begin_two_round()
    after = time.monotonic_ns()
    assert radar.pid != os.getpid() and radar.busy
    overview, raw = link.overview()
    assert raw and overview.session_id == link.session_id
    assert before <= link.exchanges[0]["started_ns"] <= link.exchanges[0]["completed_ns"] <= after
    for operation in (radar.stats, radar.stop_sensor, lambda: radar.send_config("ignored")):
        with pytest.raises(RuntimeError, match="held"):
            operation()
    with pytest.raises(TwoRoundError, match="held"):
        radar.begin_two_round()
    mask = np.zeros((overview.meta["n_frames"], overview.meta["n_samples"]), dtype=bool)
    mask[2, 4] = True
    request = encode_request(
        mask,
        session_id=link.session_id,
        sequence=1,
        range_bin_counts=overview.meta["range_bin_counts"],
    )
    cells, data = link.cells(request, overview.meta)
    assert cells.size and data == build_cells(pack(scene(6)), request)
    link.release(link.session_id)
    assert link.released and not radar.busy
    assert radar.stats() == "active=1"
    next_link = radar.begin_two_round()
    with pytest.raises(TwoRoundError, match="released"):
        link.cells(request, overview.meta)
    assert next_link.session_id != link.session_id
    next_link.release(next_link.session_id)


def test_run_two_round_releases_child_and_keeps_uart_exchange_marks(live_radar):
    radar, _events = live_radar()
    link = radar.begin_two_round()

    def analyse(raw):
        from openflight.iwr6843.dump import parse_dump

        _meta, cube = parse_dump(raw)
        return complex(cube[0, 0, 0, 0])

    result, transfer = run_two_round(link, analyse)
    assert isinstance(result, complex) and transfer.released
    assert transfer.cell_rounds >= 1 and len(link.exchanges) >= 3
    assert [e["op"] for e in link.exchanges][0] == "overview"
    assert link.exchanges[-1]["op"] == "release"
    assert not radar.busy


def test_close_releases_held_capture_before_closing_port(live_radar):
    radar, events = live_radar()
    radar.begin_two_round()
    radar.close()
    assert events[-1] == {"event": "closed", "frozen": False}


def test_failed_release_does_not_unlock_or_claim_rearm(live_radar):
    radar, _events = live_radar(fail_release=True)
    link = radar.begin_two_round()
    with pytest.raises(TwoRoundError, match="rearm failed") as raised:
        link.release(link.session_id)
    assert raised.value.raw.startswith(b"Error:")
    assert not link.released and radar.busy
    with pytest.raises(RuntimeError, match="held"):
        radar.stats()


def test_child_rejects_stale_lease_without_releasing_new_capture(live_radar):
    radar, _events = live_radar()
    first = radar.begin_two_round()
    first.release(first.session_id)
    current = radar.begin_two_round()
    with pytest.raises(TwoRoundError, match="stale"):
        radar._call(
            {"op": "two_round_release", "lease": first._lease, "session_id": first.session_id}, 2
        )
    assert radar.busy
    current.release(current.session_id)


def test_child_eof_releases_known_session(live_radar):
    radar, _events = live_radar()
    radar.begin_two_round()
    radar._process.stdin.close()
    assert radar._process.wait(timeout=5) == 0


def test_child_failure_preserves_raw_protocol_bytes_and_clock_marks(live_radar):
    radar, _events = live_radar(fail_release=True)
    link = radar.begin_two_round()
    _result, transfer = run_two_round(link, lambda _raw: None)
    assert not transfer.released and "rearm failed" in transfer.error
    assert transfer.uart_exchanges[-1]["op"] == "release"
    assert transfer.uart_exchanges[-1]["received_bytes"] > 0


@pytest.mark.parametrize("echo", [False, True])
def test_successful_overview_retry_keeps_failed_bytes_and_attempt_times(live_radar, echo):
    radar, _events = live_radar(corrupt_overviews=1, echo=echo)
    link = radar.begin_two_round()
    overview, good = link.cached_overview
    assert len(link.failed_payloads) == 1
    failed = link.failed_payloads[0]
    prefix = b"overview\n" if echo else b""
    suffix = b"\nDone\nl3dump:/>"
    assert failed.startswith(prefix + b"ILO1") and failed.endswith(suffix)
    damaged = bytearray(good)
    damaged[-9] ^= 0x5A
    assert failed == prefix + damaged + suffix
    with pytest.raises(TwoRoundError, match="CRC"):
        parse_overview(failed[len(prefix) : -len(suffix)])
    timing = link.exchanges[0]
    attempts = timing["attempts"]
    assert len(attempts) == 2 and "CRC" in attempts[0]["error"]
    assert "error" not in attempts[1]
    assert timing["received_bytes"] == sum(a["received_bytes"] for a in attempts)
    assert timing["written_bytes"] == sum(a["written_bytes"] for a in attempts) == 18
    assert attempts[0]["received_bytes"] == len(failed)
    assert attempts[1]["received_bytes"] == len(good) + len(prefix) + len(suffix)
    assert timing["started_ns"] <= attempts[0]["started_ns"]
    assert attempts[0]["completed_ns"] <= attempts[1]["started_ns"]
    assert attempts[1]["completed_ns"] <= timing["completed_ns"]
    assert parse_overview(good).session_id == overview.session_id
    link.release(link.session_id)
    assert not radar.busy


@pytest.mark.parametrize("echo", [False, True])
def test_exhausted_overview_retries_keep_both_attempts_and_hold_session(live_radar, echo):
    radar, _events = live_radar(corrupt_overviews=2, echo=echo)
    with pytest.raises(TwoRoundError, match="CRC") as raised:
        radar.begin_two_round()
    timing = raised.value.timing
    attempts = timing["attempts"]
    assert len(attempts) == 2 and all("CRC" in a["error"] for a in attempts)
    assert timing["received_bytes"] == len(raised.value.raw)
    assert timing["received_bytes"] == sum(a["received_bytes"] for a in attempts)
    assert timing["written_bytes"] == 18
    at = 0
    for attempt in attempts:
        raw = raised.value.raw[at : at + attempt["received_bytes"]]
        assert raw.startswith(b"overview\nILO1" if echo else b"ILO1")
        assert raw.endswith(b"\nDone\nl3dump:/>")
        at += attempt["received_bytes"]
    assert radar.busy
    with pytest.raises(RuntimeError, match="held"):
        radar.stats()


def test_handshake_child_runs_real_binary_rounds_and_keeps_acceptance_events(live_radar):
    radar, events = live_radar(command_handshake=True)
    link = radar.begin_two_round()
    result, transfer = run_two_round(link, lambda raw: parse_dump(raw)[1][0, 0, 0, 0])
    assert result is not None and transfer.released and not radar.busy
    rows = [row for row in events if row["event"] == "command_acceptance"]
    assert [row["command"].split()[0] for row in rows] == ["overview", "cells", "release"]
    assert all(row["first_byte_echo_verified"] and row["remainder_write_called"] for row in rows)


def test_failed_handshake_preserves_raw_echo_across_rpc_without_retry(live_radar):
    radar, events = live_radar(command_handshake=True, acceptance_failure_on="o")
    with pytest.raises(CommandAcceptanceError) as raised:
        radar.begin_two_round()
    assert raised.value.raw == b"?" and radar.busy
    assert raised.value.timing["received_bytes"] == 1
    assert raised.value.timing["written_bytes"] == raised.value.written_bytes == 1
    assert len(raised.value.timing["attempts"]) == 1
    rows = [row for row in events if row["event"] == "command_acceptance"]
    assert len(rows) == 1 and not rows[0]["remainder_write_called"]


def test_failed_cells_handshake_does_not_retry_recover_or_claim_release(live_radar):
    radar, events = live_radar(command_handshake=True, acceptance_failure_on="c")
    link = radar.begin_two_round()
    from openflight.iwr6843.dump import parse_dump
    from openflight.iwr6843.two_round import TwoRoundTransferError

    with pytest.raises(TwoRoundTransferError) as raised:
        run_two_round(link, lambda raw: parse_dump(raw)[1][0, 0, 0, 0])
    assert not raised.value.transfer.released and radar.busy
    assert raised.value.transfer.fallback is None
    assert link.failed_payloads[0] == b"?"
    rows = [row for row in events if row["event"] == "command_acceptance"]
    assert len(rows) == 2 and rows[-1]["command"].startswith("cells ")
