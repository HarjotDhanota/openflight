"""Opt-in: recorded dumps give the full dump's numbers through the simulated two-round radar.

The recordings are local and not in the repo, so this runs only when
``OPENFLIGHT_TWO_ROUND_ROWS`` names the stage-1 ``rows.jsonl`` that lists them
(``OPENFLIGHT_TWO_ROUND_LIMIT`` dumps, default 4; 0 for all 64, about 15 minutes).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from openflight.iwr6843 import two_round
from openflight.iwr6843.dump import parse_dump
from tests.test_iwr6843_two_round_firmware import SESSION, probe as probe

ROWS = os.environ.get("OPENFLIGHT_TWO_ROUND_ROWS")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "iwr6843" / "two_round_equivalence.py"

# the ±1 LSB noise floor of the stage-1 studies is far above these
TOLERANCE = {
    "launch_deg": 0.01,
    "horizontal_deg": 0.01,
    "range_cm": 0.01,
    "path_deg": 0.01,
    "attack_deg": 0.01,
    "impact_ms": 0.001,
}


def _script():
    spec = importlib.util.spec_from_file_location("two_round_equivalence", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_simulated_transfer_is_enabled_without_hardware():
    script = _script()
    supported = script._Monitor(script._Capture(None, two_round_link=object()))
    assert supported.two_round_support == {"supported": True, "reason": None}
    full_only = script._Monitor(script._Capture(b"raw"))
    assert full_only.two_round_support["supported"] is False


@pytest.mark.skipif(not ROWS or not Path(ROWS).is_file(), reason="recorded dump rows not supplied")
def test_c_packets_match_recorded_capture_reference(request):
    run_native = request.getfixturevalue("probe")
    shots = _script().load_shots(Path(ROWS))
    for shot in shots:
        raw = shot.path.read_bytes()
        result, (overview, cells), wire = run_native(raw)
        assert result.returncode == 0, (shot.path.name, result.stderr)
        assert overview == two_round.build_overview(raw, session_id=SESSION), shot.path.name
        meta = parse_dump(raw)[0]
        request = two_round.decode_request(
            wire, range_bin_counts=meta["range_bin_counts"], n_samples=meta["n_samples"]
        )
        assert cells == two_round.build_cells(raw, request), shot.path.name


@pytest.mark.skipif(
    not ROWS or not Path(ROWS).is_file(),
    reason="set OPENFLIGHT_TWO_ROUND_ROWS to the stage-1 rows.jsonl to run on recorded dumps",
)
def test_recorded_dumps_match_the_full_dump():
    script = _script()
    shots = script.load_shots(Path(ROWS))
    limit = int(os.environ.get("OPENFLIGHT_TWO_ROUND_LIMIT", "4"))
    if limit:
        shots = shots[:limit]
    for shot in shots:
        full, tee = script.full_numbers(shot, "config/iwr6843_calibration_reference.json")
        two, transfer = script.two_round_numbers(
            shot, "config/iwr6843_calibration_reference.json", tee
        )
        compared = script.compare(full, two)
        assert compared["discrete_identical"], (shot.path.name, compared["discrete_differ"])
        for metric, diff in compared["abs_diff"].items():
            assert diff <= TOLERANCE[metric], (shot.path.name, metric, diff)
        assert transfer["released"] and not transfer["radar_still_frozen"]
        assert transfer["link_bytes"] < script.FULL_DUMP_BYTES
