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

ROWS = os.environ.get("OPENFLIGHT_TWO_ROUND_ROWS")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "iwr6843" / "two_round_equivalence.py"

pytestmark = pytest.mark.skipif(
    not ROWS or not Path(ROWS).is_file(),
    reason="set OPENFLIGHT_TWO_ROUND_ROWS to the stage-1 rows.jsonl to run on recorded dumps",
)

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
