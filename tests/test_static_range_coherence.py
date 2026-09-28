"""Coherent differencing recovers a ball that power differencing hides in a clutter bin."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

from openflight.iwr6843.dump import pack_dump

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "static_range_coherence.py"
SPEC = importlib.util.spec_from_file_location("static_range_coherence", SCRIPT)
COHERENCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = COHERENCE
SPEC.loader.exec_module(COHERENCE)

SAMPLES = np.arange(128)


def _tone(bin_index: float, amplitude: float, phase: float = 0.0) -> np.ndarray:
    return amplitude * np.exp(1j * (2 * np.pi * bin_index * SAMPLES / 128 + phase))


def _cube(tones, rotation) -> np.ndarray:
    """3 TX x 12 loops x 4 RX; each virtual channel gets its own capture-level rotation."""
    rng = np.random.default_rng(3)
    cube = np.zeros((6, 36, 4, 128), dtype=complex)
    for chirp in range(36):
        for rx in range(4):
            channel = rotation[chirp % 3, rx]
            cube[:, chirp, rx] = channel * sum(tones)
    return cube + rng.normal(0, 0.3, cube.shape) + 1j * rng.normal(0, 0.3, cube.shape)


def _record(directory: Path, name: str, raw: bytes) -> None:
    (directory / f"{name}.l3dump").write_bytes(raw)
    (directory / f"{name}.json").write_text(
        json.dumps(
            {
                "usable": True,
                "artifacts": {"raw": {"path": f"{name}.l3dump"}},
                "inputs": {
                    "radar_config": {"sha256": "a" * 64},
                    "rig_geometry": {"sha256": "b" * 64},
                },
            }
        ),
        encoding="utf-8",
    )


def test_coherent_difference_ranks_a_phase_cancelled_ball_first(tmp_path):
    clutter = [_tone(20, 400.0), _tone(35, 900.0), _tone(28, 120.0, phase=0.4)]
    ball = _tone(28, 60.0, phase=0.4 + np.pi)
    empty_rotation = np.ones((3, 4), dtype=complex)
    present_rotation = np.exp(1j * np.radians(np.full((3, 4), 18.0) + np.arange(12).reshape(3, 4)))
    epoch = tmp_path / "setup-synthetic"
    (epoch / "iwr").mkdir(parents=True)
    _record(
        epoch / "iwr", "empty-000001", pack_dump(_cube(clutter, empty_rotation), n_tx=3, version=3)
    )
    _record(
        epoch / "iwr",
        "ball_present-000002",
        pack_dump(_cube([*clutter, ball], present_rotation), n_tx=3, version=3),
    )

    result = COHERENCE.analyse_epoch(epoch, bias_m=0.0, tape_m=28 * 6.0 / 128)

    assert result["selector_status"] != "accepted"
    assert result["coherent_top_bin"] == 28
    assert result["tape_bin_coherent_rank"] == 1
    assert result["coherent_top_contrast"] > 5.0
    assert np.allclose(np.array(result["phase_deg"])[:, 0], [18.0, 22.0, 26.0], atol=0.5)
