"""A saved partial dump is labelled incomplete everywhere and never replayed as a dump.

The monitor keeps a failed transfer's bytes as ``*.partial.l3dump`` and records
them as the event's ``partial_capture``, never as its ``capture_path``. The
analysis side (replay locator, pipeline check, raw replay) is checked in
``test_partial_dump_analysis`` and ``test_replay_raw_fusion_cli``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.dump import (
    PARTIAL_DUMP_SUFFIX,
    declared_dump_nbytes,
    is_partial_dump_path,
    pack_dump,
)
from openflight.iwr6843.replay import input_from_dump, inputs_from_session

RUN = "/data/openflight_sessions/run-01"


def _raw() -> bytes:
    return pack_dump(np.ones((2, 4, 4, 8), dtype=complex), n_tx=2, version=3)


def partial_event(session_dir: Path) -> dict:
    raw = _raw()
    name = f"iwr6843_20261024_101500_000_007{PARTIAL_DUMP_SUFFIX}"
    (session_dir / "iwr6843").mkdir(parents=True, exist_ok=True)
    (session_dir / "iwr6843" / name).write_bytes(raw[:-100])
    return {
        "type": "iwr6843_capture",
        "shot_number": 7,
        "ball_speed_mph": 120.0,
        "capture_path": None,
        "capture_bytes": 0,
        "capture_complete": False,
        "capture_error": f"IWR6843 dump ended early ({len(raw) - 100} of {len(raw)} bytes)",
        "partial_capture": {
            "path": f"{RUN}/iwr6843/{name}",
            "bytes": len(raw) - 100,
            "expected_bytes": len(raw),
            "complete": False,
        },
    }


def test_partial_names_and_header_sizes():
    raw = _raw()
    assert is_partial_dump_path("x/iwr6843_001.partial.l3dump")
    assert not is_partial_dump_path("x/iwr6843_001.l3dump")
    assert declared_dump_nbytes(raw) == len(raw)
    assert declared_dump_nbytes(raw[:-100]) == len(raw)
    assert declared_dump_nbytes(raw[:10]) is None


def test_lcmf_replay_never_takes_a_partial_dump(tmp_path):
    event = partial_event(tmp_path)
    session = tmp_path / "session_20261024_101500_arm.jsonl"
    session.write_text(json.dumps(event) + "\n", encoding="utf-8")

    assert inputs_from_session(session) == []
    partial_file = next((tmp_path / "iwr6843").glob(f"*{PARTIAL_DUMP_SUFFIX}"))
    with pytest.raises(ValueError, match="incomplete"):
        input_from_dump(partial_file, ball_speed_mph=120.0)
