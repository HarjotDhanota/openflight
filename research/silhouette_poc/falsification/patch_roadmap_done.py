"""Mark the working log's roadmap rows that were executed on 2026-08-27.

The log's roadmap was written as a to-do list; six of its rows have since been
done. The rows stay -- their justifications are still the record of why the
work happened -- but each completed one is labelled with its outcome so the
list reads as a status, not a stale plan.
"""

from __future__ import annotations

import os
from pathlib import Path

PAGE = Path(os.environ["OPENFLIGHT_PAGE_OUT"]) / "openflight-impact-location.html"
s = PAGE.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global s
    assert old in s, f"NOT FOUND: {old[:70]!r}"
    assert s.count(old) == 1, f"AMBIGUOUS ({s.count(old)}): {old[:70]!r}"
    s = s.replace(old, new, 1)


if "roadmap-update-2026-08-27" in s:
    raise SystemExit("already patched")

# A dated note at the top of the roadmap panel.
sub(
    "<p>Ordered by what unblocks what. Each row carries the measurement behind it "
    "&mdash; if a row has no number, it does not belong on this list.</p>",
    "<p>Ordered by what unblocks what. Each row carries the measurement behind it "
    "&mdash; if a row has no number, it does not belong on this list.</p>\n"
    '  <p id="roadmap-update-2026-08-27"><b>Update 2026-08-27:</b> six rows below were '
    "executed and are marked <b>DONE</b> with their outcome in place. The headline "
    "results: the club-metric Trackman comparison is <b>shipped</b> and waiting on a "
    "truth session; the fused radar+camera velocity <b>validates</b> against the OPS243 "
    "at 0.97&ndash;1.00; orientation still fails its physical envelope on every shot, "
    "and the corrected impact timing (see the banner at the top of this page) superseded "
    "the fits that preceded it.</p>",
)

DONE = '<span class="t-ok">[DONE 2026-08-27]</span> '
sub(
    "<td><b>Extend the Trackman comparison to CLUB metrics</b></td>",
    f"<td>{DONE}<b>Extend the Trackman comparison to CLUB metrics</b> &mdash; shipped, "
    "12 metrics, 43 tests; needs a Trackman session for truth.</td>",
)
sub(
    "<td><b>Refit orientation under the radar range ramp</b></td>",
    f"<td>{DONE}<b>Refit orientation under the radar range ramp</b> &mdash; done; "
    "0 of 6 shots inside the physical envelope, so range was necessary but not "
    "sufficient.</td>",
)
sub(
    "<td><b>Pin the rotation axis from radar + camera instead of fitting it</b></td>",
    f"<td>{DONE}<b>Pin the rotation axis from radar + camera</b> &mdash; done; the fused "
    "velocity magnitude matches the OPS243 club speed at 0.97&ndash;1.00 &plusmn; 0.03, "
    "and the fit dropped from 5 free parameters to 4.</td>",
)
sub(
    "<td><b>Open the 22 raw radar captures</b></td>",
    f"<td>{DONE}<b>Open the 22 raw radar captures</b> &mdash; done; see the ISAR row "
    "below for what they showed.</td>",
)
sub(
    "<td><b>Estimate clubhead rotation from radar Doppler (ISAR)</b></td>",
    f"<td>{DONE}<b>Estimate clubhead rotation from radar Doppler (ISAR)</b> &mdash; "
    "measured: Doppler width 1.95 bins vs a 1.27 point-target floor, close to the "
    "rotation-predicted amount, but the speed-scaling test has no power on this "
    "session; unconfirmed pending a wide-speed capture.</td>",
)
sub(
    "<td><b>Surface the trajectory metrics we already compute</b></td>",
    f"<td>{DONE}<b>Surface the trajectory metrics we already compute</b> &mdash; "
    "shipped; apex, lateral, flight time, landing speed/angle and total now reach the "
    "Shot and the UI.</td>",
)

PAGE.write_text(s, encoding="utf-8")
print("roadmap rows marked done")
