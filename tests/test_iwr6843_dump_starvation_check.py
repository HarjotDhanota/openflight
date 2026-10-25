"""The Pi check for dump starvation reads through the serial process and counts loss."""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path

from openflight.iwr6843.serial_process import IWR6843SerialProcess
from scripts.iwr6843 import dump_starvation_check as check
from tests import iwr6843_line_rate_fake as line

REPO = Path(__file__).resolve().parents[1]
PROMPT = b"Done\r\nl3dump:/>"  # the firmware's reply after the dump


def test_a_whole_dump_is_complete_with_nothing_lost():
    row = check.one_dump(line.build_radar(frames=2), timeout_s=10.0, stall_tolerance_s=0.5)
    assert row["complete"] and row["lost"] == 0
    assert row["expected"] == row["received"] == len(line.make_dump(2))
    assert row["cli_ready"] and row["transfer_s"] > 0


def test_a_dump_that_ended_early_counts_its_lost_bytes():
    row = check.one_dump(
        line.build_radar(frames=4, mode="lose_bytes"), timeout_s=10.0, stall_tolerance_s=0.5
    )
    assert not row["complete"]
    # the trailing prompt fills the tail of a short read, as it does on the wire
    assert row["lost"] == 5_000 - len(PROMPT)
    assert "ended early" in row["error"]


def test_the_check_reads_in_the_serial_process_not_its_own():
    rows: list[dict] = []
    radar = IWR6843SerialProcess(
        port="/dev/fake-iwr6843",
        radar_factory=line.FACTORY,
        factory_kwargs={"frames": 2},
        popen=functools.partial(subprocess.Popen, cwd=REPO),
    )
    try:
        check.run_loads(radar, "serial_process", ("idle",), 1, rows.append, between_s=0.0)
    finally:
        radar.close()

    assert rows[0]["reader_pid"] != rows[0]["script_pid"]
    assert rows[0]["complete"] and rows[0]["lost"] == 0
    assert check.summary(rows) == {"serial_process/idle": {"dumps": 1, "incomplete": 0, "lost": 0}}
