"""Field-derived partial dumps: the 24 Oct 2026 shot-14 capture with bytes taken out.

Shot 14 was the session's one complete dump (vertical launch 13.57 deg, launch range
1.908 m). Runs are removed the way that session lost them: whole 256-byte units,
totals drawn from its 32 logged shortfalls. The dump stays in the pi-handoff folder
beside the checkout (or ``OPENFLIGHT_PI_HANDOFF``); without it these tests skip.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.partial_dump import PARTIAL_LABEL, recover_partial_dump, select_frames
from scripts.iwr6843 import partial_dump_simulation as sim

REPO = Path(__file__).resolve().parents[1]
HANDOFF = Path(os.environ.get("OPENFLIGHT_PI_HANDOFF") or REPO.parent / "pi-handoff")
RUN = HANDOFF / "Harjot-outdoor-test-1" / "arm5" / "paired" / "run-01"
SHOT_14 = RUN / "iwr6843" / "iwr6843_20261024_172814_013_013.l3dump"
SESSION = RUN / "session_20261024_171945_arm5.jsonl"

pytestmark = pytest.mark.skipif(
    not (SHOT_14.exists() and SESSION.exists()),
    reason="the 24 Oct shot-14 dump is not here: set OPENFLIGHT_PI_HANDOFF",
)


@pytest.fixture(scope="module", name="shot")
def fixture_shot():
    raw = SHOT_14.read_bytes()
    meta, cube = parse_dump(raw)
    return raw, meta, cube


def test_the_field_shortfalls_are_whole_256_byte_units():
    units = sim.field_loss_units()

    assert len(units) == 32
    assert min(units) == 5 and max(units) == 143


def test_field_like_losses_never_place_a_sample_wrongly(shot):
    raw, meta, cube = shot
    rng = np.random.default_rng(20261024)
    kept = untouched = 0
    for units in sim.field_loss_units():
        partial, runs = sim.delete_runs(
            raw, rng, total_units=units, n_runs=int(rng.integers(1, 5)), quantum=256
        )
        recovery = recover_partial_dump(partial)
        untouched += meta["n_frames"] - len(sim.frames_touched(meta, runs))
        if not recovery.usable:
            continue
        _meta, recovered = parse_dump(recovery.raw)
        for index, frame in enumerate(recovery.frames_kept):
            np.testing.assert_array_equal(recovered[index], cube[frame])
        kept += len(recovery.frames_kept)

    assert kept >= 0.95 * untouched


def test_a_late_run_leaves_shot_14_s_range_and_a_labelled_launch(shot):
    raw, meta, _cube = shot
    shot_context = sim.shot_contexts(SESSION)[SHOT_14.name]
    offset = 22 * (len(raw) - 140) // meta["n_frames"] + 4_000  # inside frame 22, after the flight
    partial, _runs = sim.delete_runs_at(raw, [(offset, 3 * 256)])

    recovery = recover_partial_dump(partial)
    result = sim.run_runtime(None, shot_context, partial_raw=partial)

    assert recovery.frames_dropped == (22,)
    assert result["partial_recovery"]["label"] == PARTIAL_LABEL
    assert result["range_label"] == PARTIAL_LABEL
    assert result["range_m"] == pytest.approx(1.908, abs=0.05)
    assert result["launch_deg"] is not None
    assert result["launch_status"].endswith(PARTIAL_LABEL)
    # the same frames cut from the whole dump give the same answer: nothing was guessed
    assert sim.run_runtime(select_frames(raw, recovery.frames_kept), shot_context)[
        "launch_deg"
    ] == pytest.approx(result["launch_deg"])


def test_the_recovery_script_reports_and_writes_a_partial_named_file(shot, tmp_path, capsys):
    from openflight.iwr6843.dump import is_partial_dump_path  # noqa: PLC0415
    from scripts.iwr6843 import recover_partial_dump as script  # noqa: PLC0415

    raw, _meta, _cube = shot
    partial, _runs = sim.delete_runs_at(raw, [(300_000, 4 * 256)])
    saved = tmp_path / "iwr6843_20261024_172814_013_013.partial.l3dump"
    saved.write_bytes(partial)

    assert script.main([str(saved), "--write"]) == 0

    record = json.loads(capsys.readouterr().out)
    written = Path(record["written"])
    assert record["status"] == "recovered" and record["lost_bytes"] == 1024
    assert written.name == "iwr6843_20261024_172814_013_013.recovered.partial.l3dump"
    assert is_partial_dump_path(written)
    assert recover_partial_dump(written.read_bytes()).status == "complete"
