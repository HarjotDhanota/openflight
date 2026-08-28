"""Bring the orientation block up to date with sections 13-16.

The block was written before three things were measured, and one of its
statements is now actively misleading: it names a tape-known target as "the
single most useful next measurement". That is still worth doing, but it is no
longer top of the list -- no accuracy figure for the club can be validated at
all until the Trackman comparison covers club metrics, which today it does not.
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


# 1. What the last two days changed, stated once, near the top.
sub(
    "<p><b>Please do not quote.</b>",
    "<p><b>Three things were measured after the rest of this page was written, "
    "and they change how to read it.</b> <b>(1)</b> The fit is <b>noise-limited, "
    "not objective-limited</b> &mdash; one pixel of segmentation error is worth "
    "about <b>ten degrees</b> of face angle, and the first 5&deg; of face angle "
    "change the silhouette by <em>zero</em> pixels (&sect;13). <b>(2)</b> Every fit "
    "here renders the clubhead at the range of the <em>ball</em>; the radar says "
    "the club sweeps <b>529&nbsp;mm</b> through that value during the frames being "
    "fitted, and constant range is <b>rejected at p &asymp; 0.04</b> (&sect;14). "
    "<b>(3)</b> Replacing the fit metric is a <b>closed question</b> &mdash; IoU and "
    "chamfer disagree on the pose by a median of <b>12.7&deg;</b>, which is the same "
    "scale as the segmentation noise, so neither is the problem (&sect;13).</p>\n"
    "<p><b>Please do not quote.</b>",
)

# 2. The "single most useful next measurement" is no longer the right answer.
sub(
    "<p><b>The single most useful next measurement</b> is not another estimator. It is a "
    "target at a tape-known position visible to both sensors, which would close the 5&deg; "
    "disagreement, plus a lux and exposure ladder in the actual bay, which decides whether "
    "the optical route is a one-degree instrument or a four-degree one.</p>",
    "<p><b>The single most useful next piece of work</b> is not another estimator, and it "
    "is no longer the tape-known target either. It is <b>extending the Trackman comparison "
    "to club metrics</b>: <code>compare_trackman.py</code> checks ball speed, club speed, "
    "smash, launch angles, spin and carry, and <b>no club data whatsoever</b> &mdash; no "
    "face angle, club path, attack angle or impact location. Until that changes, nothing "
    "this project produces about the club can be checked against truth, only against its "
    "own internal consistency. After that: a tape-known target visible to both sensors to "
    "close the 5&deg; disagreement, and a lux and exposure ladder in the actual bay. Full "
    'list, ordered by what unblocks what, in <a href="#roadmap">What has to happen '
    "next</a>.</p>",
)

PAGE.write_text(s, encoding="utf-8")
print("status block updated")
print("  mentions s13/s14:", "&sect;13" in s and "&sect;14" in s)
print("  links to roadmap:", 'href="#roadmap"' in s)
