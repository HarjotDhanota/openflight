"""Compile the pure firmware core and compare its actual packets with the host contract."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import zlib
from pathlib import Path, PurePosixPath

import numpy as np
import pytest

from openflight.iwr6843 import two_round
from openflight.iwr6843.dump import pack_dump, parse_dump
from tests.iwr6843_partial_scene import pack, scene
from tests.test_iwr6843_dump_evidence import _evidence

ROOT = Path(__file__).resolve().parents[1]
SESSION = 0x1234ABCD


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    folder = tmp_path_factory.mktemp("two-round-c")
    compiler = shutil.which("cc")
    image = os.environ.get("OPENFLIGHT_FIRMWARE_DOCKER_IMAGE")
    docker = []
    include, source, executable = (
        ROOT / "firmware/iwr6843",
        ROOT / "tests/fixtures/iwr_two_round_probe.c",
        folder / "probe",
    )
    if compiler is None:
        if not image or not shutil.which("docker"):
            pytest.skip("needs cc, or OPENFLIGHT_FIRMWARE_DOCKER_IMAGE with GCC")
        docker = [
            "docker",
            "run",
            "--rm",
            "--mount",
            f"type=bind,source={ROOT},target=/repo,readonly",
            "--mount",
            f"type=bind,source={folder},target=/case",
            image,
        ]
        compiler, include, source, executable = (
            "gcc",
            "/repo/firmware/iwr6843",
            "/repo/tests/fixtures/iwr_two_round_probe.c",
            "/case/probe",
        )
    subprocess.run(
        [
            *docker,
            compiler,
            "-std=c99",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-function",
            "-fsanitize=undefined",
            "-fno-sanitize-recover=all",
            "-I",
            str(include),
            str(source),
            "-lm",
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    def run(raw, request=None, *, fail_after=-1, fields=None):
        meta, _ = parse_dump(raw)
        prefix_bytes = meta["header_nbytes"] + meta["frame_metadata_nbytes"]
        samples_bytes = sum(meta["range_bin_counts"]) * meta["chirps_per_frame"] * 4 * 4
        prefix = raw[:prefix_bytes]
        suffix = raw[prefix_bytes + samples_bytes :]
        values = [
            meta["n_frames"],
            meta["chirps_per_frame"] // 3,
            meta["n_samples"],
            len(prefix),
            len(suffix),
            SESSION,
        ]
        for index, value in (fields or {}).items():
            values[index] = value
        descriptors = bytes(
            value
            for pair in zip(meta["range_bin_starts"], meta["range_bin_counts"])
            for value in pair
        )
        data = (
            struct.pack("<6I", *values)
            + descriptors
            + prefix
            + suffix
            + raw[prefix_bytes : prefix_bytes + samples_bytes]
        )
        if request is None:
            mask = np.zeros((meta["n_frames"], meta["n_samples"]), bool)
            mask[0, 0] = mask[-1, meta["range_bin_counts"][-1] - 1] = True
            request = two_round.encode_request(
                mask, session_id=SESSION, sequence=3, range_bin_counts=meta["range_bin_counts"]
            ).data
        (folder / "input").write_bytes(data)
        (folder / "request").write_bytes(request)
        output = [folder / "overview", folder / "cells"]
        for path in output:
            path.unlink(missing_ok=True)
        base = PurePosixPath("/case") if docker else folder
        result = subprocess.run(
            [
                *docker,
                str(executable),
                str(base / "input"),
                str(base / "request"),
                str(base / "overview"),
                str(base / "cells"),
                str(fail_after),
            ],
            capture_output=True,
            text=True,
        )
        return result, [p.read_bytes() if p.exists() else b"" for p in output], request

    return run


def _raw(kind):
    if kind in ("production", "evidence"):
        count = 24 if kind == "production" else 6
        if kind == "evidence":
            capture, frames, trailer = _evidence(count, 2)
            return pack(
                scene(count, seed=9),
                version=9,
                capture_evidence=capture,
                frame_evidence=frames,
                dump_trailer=trailer,
            )
        return pack(scene(count, seed=9))
    rng = np.random.default_rng(32)
    shape = (3, 6 if kind == "zero" else 48, 4, 7)
    cube = rng.integers(-32768, 32768, size=shape) + 1j * rng.integers(-32768, 32768, size=shape)
    if kind == "zero":
        cube[:] = 123 + 456j
    elif kind == "extremes":
        cube[:] = np.where(np.indices(shape)[1] % 2, -32768 - 32768j, 32767 + 32767j)
    return pack_dump(
        cube,
        n_tx=3,
        version=6,
        sample_fmt=4,
        frame_period_us=3000,
        range_bin_starts=[0, 3, 7],
        range_bin_counts=[7, 4, 6],
        frame_time_offsets_us=[0, 3000, 9000],
    )


@pytest.mark.parametrize("kind", ["production", "evidence", "zero", "extremes", "random"])
def test_c_packets_match_host_byte_for_byte(probe, kind):
    raw = _raw(kind)
    result, (overview, cells), wire = probe(raw)
    assert result.returncode == 0, result.stderr
    assert "crc=cbf43926" in result.stdout
    assert overview == two_round.build_overview(raw, session_id=SESSION)
    meta = parse_dump(raw)[0]
    request = two_round.decode_request(
        wire, range_bin_counts=meta["range_bin_counts"], n_samples=meta["n_samples"]
    )
    assert cells == two_round.build_cells(raw, request)


@pytest.mark.parametrize(
    "mutation", ["crc", "session", "sequence", "version", "truncated", "tail", "run"]
)
def test_c_rejects_bad_cell_requests(probe, mutation):
    raw = _raw("zero")
    _, _, wire = probe(raw)
    bad = bytearray(wire[:-4])
    if mutation == "session":
        bad[8] ^= 1
    elif mutation == "sequence":
        bad[5] = 0
    elif mutation == "version":
        bad[4] = 2
    elif mutation == "truncated":
        bad = bad[:-1]
    elif mutation == "tail":
        bad += b"x"
    elif mutation == "run":
        bad[14] = 255
    bad += struct.pack("<I", zlib.crc32(bad))
    if mutation == "crc":
        bad[-1] ^= 1
    result, _, _ = probe(raw, bytes(bad))
    assert result.returncode == 3, result.stderr


@pytest.mark.parametrize("fields", [{0: 0}, {0: 65}, {1: 0}, {1: 17}, {2: 129}, {5: 0}])
def test_c_rejects_unsupported_capture_geometry(probe, fields):
    result, _, _ = probe(_raw("zero"), fields=fields)
    assert result.returncode == 2, result.stderr


def test_c_reports_a_short_output_write(probe):
    result, (overview, cells), _ = probe(_raw("zero"), fail_after=512)
    assert result.returncode == 4 and not cells
    assert len(overview) <= 512
