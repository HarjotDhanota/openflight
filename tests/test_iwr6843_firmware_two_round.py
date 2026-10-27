"""Firmware stage-2 two-round transfer against the host reference, byte for byte.

firmware/iwr6843/two_round_core.h is host-compiled (as the firmware compiles it,
without FP contraction) with tests/iwr6843_two_round_harness.c, fed the frames of
dumps, and its overview, cells and session replies are compared with
``openflight.iwr6843.two_round`` / ``two_round_sim`` (the reference implementation).

Skips without a C compiler ($CC, cc, gcc, clang or ziglang) or without the host
two-round module. Recorded field dumps are used when OPENFLIGHT_IWR_DUMP_ROWS names
a JSONL file whose rows carry a dump ``path``; otherwise only synthetic dumps run.
"""

from __future__ import annotations

import decimal
import importlib.util
import json
import os
import re
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tests.test_iwr6843_firmware_cli_table import _compiler

two_round = pytest.importorskip("openflight.iwr6843.two_round")
two_round_sim = pytest.importorskip("openflight.iwr6843.two_round_sim")

from openflight.iwr6843.dump import parse_dump, payload_nbytes  # noqa: E402
from tests.iwr6843_partial_scene import pack, scene  # noqa: E402
from tests.test_iwr6843_dump_evidence import _evidence  # noqa: E402

REPO = Path(__file__).parents[1]
FIRMWARE_DIR = REPO / "firmware" / "iwr6843"
FORMAT = FIRMWARE_DIR / "two_round_format.h"
HARNESS = Path(__file__).parent / "iwr6843_two_round_harness.c"
EXE = ".exe" if os.name == "nt" else ""
ROWS = os.environ.get("OPENFLIGHT_IWR_DUMP_ROWS")


@pytest.fixture(name="harness", scope="module")
def _harness(tmp_path_factory) -> Path:
    exe = tmp_path_factory.mktemp("two_round") / f"harness{EXE}"
    built = subprocess.run(
        [
            *_compiler(),
            "-std=c99",
            "-O2",
            "-ffp-contract=off",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-function",
            "-I",
            str(FIRMWARE_DIR),
            str(HARNESS),
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    return exe


def _capture_file(raw: bytes, path: Path) -> Path:
    """The frozen ring as the harness reads it: the dump's frames, prefix and suffix."""
    meta, _cube = parse_dump(raw)
    offset = meta["header_nbytes"] + (meta.get("frame_metadata_nbytes") or 0)
    counts, starts = meta["range_bin_counts"], meta["range_bin_starts"]
    cpf, nrx = meta["chirps_per_frame"], meta["n_rx"]
    samples = sum(counts) * cpf * nrx * 4
    declared = meta["header_nbytes"] + payload_nbytes(meta, raw)
    prefix, suffix = raw[:offset], raw[offset + samples : declared]
    out = bytearray(
        struct.pack("<HHHH", meta["n_frames"], cpf // 3, nrx, meta["n_samples"])
    )
    for start, count in zip(starts, counts):
        out += bytes((start, count))
    out += struct.pack("<I", len(prefix)) + prefix + struct.pack("<I", len(suffix)) + suffix
    out += raw[offset : offset + samples]
    path.write_bytes(bytes(out))
    return path


def _run(harness: Path, *args) -> str:
    done = subprocess.run([str(harness), *map(str, args)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def _overview(harness, raw, tmp_path, session=0x00010034, flags=0) -> bytes:
    cap = _capture_file(raw, tmp_path / "capture.bin")
    out = tmp_path / "overview.bin"
    _run(harness, "overview", cap, f"{session:08x}", flags, out)
    return out.read_bytes()


# --------------------------------------------------------------------------- dumps

N_FRAMES = 6


def _synthetic() -> dict[str, bytes]:
    capture, frames, trailer = _evidence(N_FRAMES, 2)
    return {
        "scene_v7": pack(scene(N_FRAMES, seed=5)),
        "scene_v9_evidence": pack(
            scene(N_FRAMES, seed=6),
            version=9,
            capture_evidence=capture,
            frame_evidence=frames,
            dump_trailer=trailer,
        ),
        "scene_24_frames": pack(scene(24, seed=7)),
        "noise_only": pack(scene(12, seed=8, structure=False)),
    }


SYNTHETIC = _synthetic()


def _recorded() -> list[tuple[str, str]]:
    if not ROWS or not Path(ROWS).exists():
        return []
    rows = [json.loads(line) for line in Path(ROWS).read_text().splitlines() if line.strip()]
    return [(Path(row["path"]).name, row["path"]) for row in rows if Path(row["path"]).exists()]


RECORDED = _recorded()


# --------------------------------------------------------------------------- the contract


def _defines() -> dict[str, str]:
    text = FORMAT.read_text(encoding="utf-8")
    return dict(re.findall(r"^#define[ \t]+(\w+)[ \t]+(\S+)", text, re.M))


def _value(text: str):
    text = text.strip()
    if text.startswith('"'):
        return text.strip('"').encode()
    return int(text.rstrip("U"), 0)


def test_the_c_header_mirrors_the_host_constants():
    d = {name: _value(value) for name, value in _defines().items()}
    host = two_round

    assert d["L3_TWO_ROUND_OVERVIEW_MAGIC"] == host.OVERVIEW_MAGIC
    assert d["L3_TWO_ROUND_OVERVIEW_VERSION"] == host.OVERVIEW_VERSION
    assert d["L3_TWO_ROUND_CELLS_MAGIC"] == host.CELLS_MAGIC
    assert d["L3_TWO_ROUND_CELLS_VERSION"] == host.CELLS_VERSION
    assert d["L3_TWO_ROUND_REQUEST_MAGIC"] == host.REQUEST_MAGIC
    assert d["L3_TWO_ROUND_REQUEST_VERSION"] == host.REQUEST_VERSION
    assert d["L3_TWO_ROUND_CRC_BYTES"] == host.CRC.size
    assert d["L3TR_OV_HEADER_BYTES"] == host.OVERVIEW_HEADER.size
    assert d["L3TR_RQ_HEADER_BYTES"] == host.REQUEST_HEADER.size
    assert d["L3TR_CL_HEADER_BYTES"] == host.CELLS_HEADER.size
    assert d["L3TR_FLAG_PREVIOUS_SESSION_TIMED_OUT"] == host.FLAG_PREVIOUS_SESSION_TIMED_OUT
    assert d["L3TR_POWER_CODE_LOG16"] == host.POWER_CODE_LOG16
    assert d["L3TR_LOG16_CODES_PER_DECADE"] == host.LOG16_CODES_PER_DECADE
    assert d["L3TR_LOG16_MAX_CODE"] == host.LOG16_MAX_CODE
    assert d["L3TR_N_LOOP_TX"] == host.N_LOOP_TX
    assert d["L3TR_N_SCOPES"] == 2
    assert d["L3TR_CMD_OVERVIEW"].decode() == host.CMD_OVERVIEW
    assert d["L3TR_CMD_CELLS"].decode() == host.CMD_CELLS
    assert d["L3TR_CMD_RELEASE"].decode() == host.CMD_RELEASE
    assert d["L3TR_HOLD_TIMEOUT_MS"] == int(two_round_sim.HOLD_TIMEOUT_S * 1000)


def _offsets(fmt: str) -> list[int]:
    offsets, at = [], 0
    for count, code in re.findall(r"(\d*)([a-zA-Z])", fmt.lstrip("<")):
        offsets.append(at)
        at += struct.calcsize("<" + (count or "") + code)
    return offsets


@pytest.mark.parametrize(
    ("struct_name", "prefix", "fields"),
    [
        (
            "OVERVIEW_HEADER",
            "L3TR_OV_",
            "MAGIC VERSION HEADER_NBYTES TOTAL_NBYTES SESSION N_FRAMES CHIRPS N_TX N_RX "
            "N_SAMPLES N_SUM_BINS N_MAP_CELLS MAP_CODE N_SCOPES NOISE_BURST NOISE_WINDOW "
            "PREFIX_NBYTES SUFFIX_NBYTES FLAGS",
        ),
        ("REQUEST_HEADER", "L3TR_RQ_", "MAGIC VERSION SEQUENCE N_FRAMES SESSION"),
        (
            "CELLS_HEADER",
            "L3TR_CL_",
            "MAGIC VERSION HEADER_NBYTES TOTAL_NBYTES SESSION SEQUENCE RESERVED N_CELLS "
            "REQUEST_CRC CELL_NBYTES",
        ),
    ],
)
def test_the_c_field_offsets_match_the_host_structs(struct_name, prefix, fields):
    d = {name: _value(value) for name, value in _defines().items()}
    host = getattr(two_round, struct_name)

    c_offsets = [d[prefix + field] for field in fields.split()]

    assert c_offsets == _offsets(host.format)


# --------------------------------------------------------------------------- overview


@pytest.mark.parametrize("name", sorted(SYNTHETIC))
def test_the_overview_is_the_reference_byte_for_byte(harness, tmp_path, name):
    raw = SYNTHETIC[name]

    got = _overview(harness, raw, tmp_path, session=0x00010034, flags=1)

    assert got == two_round.build_overview(raw, session_id=0x00010034, flags=1)


@pytest.mark.skipif(not RECORDED, reason="set OPENFLIGHT_IWR_DUMP_ROWS to a rows.jsonl of dumps")
@pytest.mark.parametrize(("name", "path"), RECORDED, ids=[name for name, _ in RECORDED])
def test_recorded_dumps_give_the_reference_overview_byte_for_byte(harness, tmp_path, name, path):
    raw = Path(path).read_bytes()

    got = _overview(harness, raw, tmp_path, session=0x00ab1234)

    want = two_round.build_overview(raw, session_id=0x00AB1234)
    if got != want:
        first = next(i for i, (a, b) in enumerate(zip(got, want)) if a != b)
        pytest.fail(f"{name}: {len(got)} vs {len(want)} bytes, first difference at {first}")


def test_the_overview_parses_on_the_host(harness, tmp_path):
    raw = SYNTHETIC["scene_v9_evidence"]

    overview = two_round.parse_overview(_overview(harness, raw, tmp_path), session_id=0x00010034)

    assert overview.suffix == raw[-8:]
    assert overview.meta["capture_evidence"] == parse_dump(raw)[0]["capture_evidence"]


# --------------------------------------------------------------------------- log16


def _exact_code(p: float) -> int:
    with decimal.localcontext() as ctx:
        ctx.prec = 60
        x = decimal.Decimal(max(p, 1.0)).log10() * 5000
        code = int(x.to_integral_value(rounding=decimal.ROUND_HALF_EVEN))
    return min(max(code, 0), 0xFFFF)


def _log16(harness, tmp_path, values) -> np.ndarray:
    src = tmp_path / "doubles.bin"
    out = tmp_path / "codes.bin"
    src.write_bytes(np.asarray(values, dtype="<f8").tobytes())
    _run(harness, "log16", src, out)
    return np.frombuffer(out.read_bytes(), dtype="<u2")


def test_log16_is_exact_at_every_step_of_the_code(harness, tmp_path):
    """Every code step in reach (powers up to 1e13): the doubles on either side of it."""
    with decimal.localcontext() as ctx:
        ctx.prec = 60
        ln10 = ctx.ln(decimal.Decimal(10))
        steps = [ctx.exp(ln10 * decimal.Decimal(2 * c + 1) / 10000) for c in range(65535)]
    values, expected = [], []
    for c, step in enumerate(steps):
        nearest = float(step)
        for value in (
            np.nextafter(nearest, 0.0),
            nearest,
            np.nextafter(nearest, np.inf),
        ):
            values.append(value)
            expected.append(c + 1 if decimal.Decimal(value) > step else c)
    got = _log16(harness, tmp_path, values)

    assert got.tolist() == expected


def test_log16_agrees_with_the_host_away_from_the_steps(harness, tmp_path):
    rng = np.random.default_rng(1)
    values = np.concatenate(
        [10.0 ** rng.uniform(-2, 15, 20000), [0.0, 0.5, 1.0, 10.0, 1e13, 1e14, 1e20]]
    )

    got = _log16(harness, tmp_path, values)

    assert got.tolist() == two_round.log16_encode(values).tolist()
    assert got[-7:].tolist() == [_exact_code(v) for v in values[-7:]]


# --------------------------------------------------------------------------- cells


def _request(raw, cells, sequence=1, session=0x00010034):
    meta = parse_dump(raw)[0]
    mask = np.zeros((meta["n_frames"], meta["n_samples"]), bool)
    for frame, local in cells:
        mask[frame, local] = True
    return two_round.encode_request(
        mask, session_id=session, sequence=sequence, range_bin_counts=meta["range_bin_counts"]
    )


def _cells(harness, raw, data: bytes, tmp_path) -> bytes:
    cap = _capture_file(raw, tmp_path / "capture.bin")
    req = tmp_path / "request.bin"
    req.write_bytes(data)
    out = tmp_path / "cells.bin"
    _run(harness, "cells", cap, req, out)
    return out.read_bytes()


def test_cells_are_the_reference_byte_for_byte(harness, tmp_path):
    raw = SYNTHETIC["scene_v9_evidence"]
    request = _request(raw, [(0, 0), (0, 1), (0, 2), (2, 52), (3, 7), (5, 30), (5, 31)])

    assert _cells(harness, raw, request.data, tmp_path) == two_round.build_cells(raw, request)


def test_every_cell_of_a_capture_is_the_reference(harness, tmp_path):
    raw = SYNTHETIC["scene_24_frames"]
    meta = parse_dump(raw)[0]
    every = [(f, b) for f, count in enumerate(meta["range_bin_counts"]) for b in range(count)]
    request = _request(raw, every, sequence=4)

    assert _cells(harness, raw, request.data, tmp_path) == two_round.build_cells(raw, request)


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        (lambda d: d[:8], "truncated"),
        (lambda d: b"XXXX" + d[4:], "magic"),
        (lambda d: d[:-1] + bytes([d[-1] ^ 1]), "CRC"),
        (lambda d: d[:4] + b"\x02" + d[5:], "version"),
    ],
)
def test_damaged_requests_are_refused(harness, tmp_path, damage, reason):
    raw = SYNTHETIC["scene_v7"]
    request = _request(raw, [(1, 3)])

    got = _cells(harness, raw, damage(request.data), tmp_path)

    assert got.startswith(b"ERR ") and reason in got.decode()
    with pytest.raises(two_round.TwoRoundError):
        meta = parse_dump(raw)[0]
        two_round.decode_request(
            damage(request.data),
            range_bin_counts=meta["range_bin_counts"],
            n_samples=meta["n_samples"],
        )


# --------------------------------------------------------------------------- the session


class Session:
    """Drives the core's CLI handlers through a script; replies as the SDK CLI frames them."""

    def __init__(self, harness, raw, tmp_path):
        self.harness, self.raw, self.tmp = harness, raw, tmp_path
        self.cap = _capture_file(raw, tmp_path / "capture.bin")
        self.lines: list[str] = []

    def line(self, text):
        self.lines.append(f"L {text}")

    def send_request(self, request):
        self.lines.append(f"B {request.data.hex()}")
        self.line(f"cells {request.session_id:08x} {len(request.data)}")

    def run(self) -> tuple[list[bytes], list[str]]:
        script = self.tmp / "script.txt"
        script.write_text("\n".join(self.lines) + "\n")
        out, events = self.tmp / "out.bin", self.tmp / "events.txt"
        _run(self.harness, "session", self.cap, script, out, events)
        replies = out.read_bytes().split(b"l3dump:/>")[:-1]
        return replies, events.read_text().splitlines()


def _session(n: int) -> int:
    """The n-th session's id (0-based): counter n+1 over the harness's freeze tick bits 8-23."""
    return (n + 1) << 16 | ((0x12345600 + n * 0x10000) >> 8) & 0xFFFF


FIRST_SESSION = _session(0)


def _payload(reply: bytes) -> bytes:
    assert reply.endswith(b"\nDone\n"), reply[-40:]
    return reply[: -len(b"\nDone\n")]


def test_one_frozen_capture_serves_an_overview_several_requests_and_a_release(harness, tmp_path):
    raw = SYNTHETIC["scene_v9_evidence"]
    s = Session(harness, raw, tmp_path)
    first = _request(raw, [(0, 4), (1, 4)], sequence=1, session=FIRST_SESSION)
    second = _request(raw, [(5, 0), (5, 52)], sequence=2, session=FIRST_SESSION)
    s.line("overview")
    s.send_request(first)
    s.send_request(second)
    s.line("overview")  # a reconnecting host: the same capture, the same bytes
    s.line(f"release {FIRST_SESSION:08x}")

    replies, events = s.run()

    overview = two_round.build_overview(raw, session_id=FIRST_SESSION)
    assert _payload(replies[0]) == overview
    assert replies[1] == f"ready {len(first.data)}\n".encode() + two_round.build_cells(
        raw, first
    ) + b"\nDone\n"
    assert _payload(replies[2].split(b"\n", 1)[1]) == two_round.build_cells(raw, second)
    assert _payload(replies[3]) == overview
    assert replies[4] == f"released {FIRST_SESSION:08x}\nDone\n".encode()
    assert [e for e in events if not e.startswith("state")] == [
        "freeze",
        "stamp 0",
        "stamp 1",
        "rearm",
    ]


def test_while_frozen_every_other_command_is_refused(harness, tmp_path):
    raw = SYNTHETIC["scene_v7"]
    s = Session(harness, raw, tmp_path)
    s.line("stats")  # not frozen yet: runs
    s.line("overview")
    for command in ("clockProbe 00000001 00000002 00000003", "cp 1 2 3", "l3dump",
                    "sensorStart", "sensorStop", "captureCfg 1 2 3 4 5 6", "stats"):
        s.line(command)
    s.line("help")

    replies, events = s.run()

    refusal = f"Error: session {FIRST_SESSION:08x} frozen: only overview, cells, release\n"
    assert replies[0] == b"Done\n"
    for reply in replies[2:9]:
        assert reply == (refusal + "Error -1\n").encode()
    assert replies[9] == b"Done\n"  # help
    assert [e for e in events if e.startswith("ran")] == ["ran stats", "ran help"]


def test_stale_sessions_are_refused_and_nothing_is_rearmed(harness, tmp_path):
    raw = SYNTHETIC["scene_v7"]
    s = Session(harness, raw, tmp_path)
    s.line(f"release {FIRST_SESSION:08x}")  # nothing frozen
    s.line("overview")
    stale = _request(raw, [(0, 0)], session=FIRST_SESSION + 1)
    s.line(f"cells {FIRST_SESSION + 1:08x} {len(stale.data)}")  # stale on the command line
    s.lines.append(f"B {stale.data.hex()}")
    s.line(f"cells {FIRST_SESSION:08x} {len(stale.data)}")  # stale inside the request
    s.line("release 00000001")

    replies, events = s.run()

    frozen = f"(frozen {FIRST_SESSION:08x})"
    assert replies[0] == b"Error: no frozen capture\nError -1\n"
    assert replies[2] == f"Error: stale session {FIRST_SESSION + 1:08x} {frozen}\nError -1\n".encode()
    assert replies[3].endswith(
        f"Error: stale session {FIRST_SESSION + 1:08x} {frozen}\nError -1\n".encode()
    )
    assert replies[4] == f"Error: stale session 00000001 {frozen}\nError -1\n".encode()
    assert "rearm" not in events
    assert events[-1] == f"state frozen=1 session={FIRST_SESSION:08x} timed_out=0"


def test_a_request_whose_bytes_do_not_arrive_is_refused_and_the_capture_kept(harness, tmp_path):
    raw = SYNTHETIC["scene_v7"]
    s = Session(harness, raw, tmp_path)
    s.line("overview")
    s.line(f"cells {FIRST_SESSION:08x} 40")  # no bytes queued
    s.line(f"cells {FIRST_SESSION:08x} 9999999")

    replies, events = s.run()

    assert replies[1] == b"ready 40\nError: bad request: the request bytes did not arrive\nError -1\n"
    assert replies[2].startswith(b"Error: bad request: usage")
    assert events[-1].startswith("state frozen=1")


def test_the_hold_timeout_rearms_and_flags_the_next_overview(harness, tmp_path):
    raw = SYNTHETIC["scene_v7"]
    s = Session(harness, raw, tmp_path)
    s.line("overview")
    s.lines += ["T 20000", "T 10000"]  # 30 s: still held
    s.line("overview")  # activity
    s.lines += ["T 29000", "T 2000"]  # 31 s idle: rearmed
    s.line(f"release {FIRST_SESSION:08x}")
    s.line("overview")  # the next shot

    replies, events = s.run()

    second = _session(1)
    assert events.count("timeout") == 1
    assert replies[2] == b"Error: no frozen capture\nError -1\n"
    assert _payload(replies[3]) == two_round.build_overview(
        raw, session_id=second, flags=two_round.FLAG_PREVIOUS_SESSION_TIMED_OUT
    )


def test_a_failed_freeze_leaves_nothing_frozen(harness, tmp_path):
    raw = SYNTHETIC["scene_v7"]
    s = Session(harness, raw, tmp_path)
    s.lines.append("F two-round needs an iq16 capture")
    s.line("overview")
    s.line("stats")

    replies, events = s.run()

    assert replies[0] == b"Error: two-round needs an iq16 capture\nError -1\n"
    assert replies[1] == b"Done\n"
    assert "ran stats" in events


def test_session_ids_are_a_counter_over_the_freeze_tick_and_never_repeat(harness, tmp_path):
    raw = SYNTHETIC["scene_v7"]
    s = Session(harness, raw, tmp_path)
    ids = []
    for n in range(5):
        session = _session(n)
        ids.append(session)
        s.line("overview")
        s.line(f"release {session:08x}")

    replies, _events = s.run()

    assert len(set(ids)) == 5
    for n, session in enumerate(ids):
        assert replies[2 * n + 1] == f"released {session:08x}\nDone\n".encode()


def test_the_simulated_radar_refuses_the_same_commands(tmp_path):
    """The reference's session rules, for the cases the firmware tests above cover."""
    sim = two_round_sim.SimulatedIWR6843TwoRound(first_session_id=FIRST_SESSION)
    raw = SYNTHETIC["scene_v7"]
    sim.freeze(raw)

    assert sim.command(b"l3dump").startswith(
        f"Error: session {FIRST_SESSION:08x} frozen: only overview, cells, release\n".encode()
    )
    assert sim.command(b"release 00000001").startswith(
        f"Error: stale session 00000001 (frozen {FIRST_SESSION:08x})\n".encode()
    )
    assert importlib.util.find_spec("openflight.iwr6843.two_round_sim") is not None
