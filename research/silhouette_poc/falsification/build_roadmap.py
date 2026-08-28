"""Add a tracked "what has to happen next" list to the top of the page.

The page is a working log. It records what was tried and what it measured, but
a reader who wants to HELP has to reverse-engineer the to-do list from thirty
sections of narrative. This inserts that list explicitly, ordered by what
unblocks what rather than by how interesting it is.

Every row carries the measurement that justifies it, so nobody has to take the
priority on trust. Rows with no measurement behind them do not belong here.
"""

from __future__ import annotations

import os
from pathlib import Path

PAGE = (
    Path(
        os.environ.get("OPENFLIGHT_PAGE_OUT", Path(__file__).resolve().parent / "page")
    )
    / "openflight-impact-location.html"
)

ROADMAP = """
<div class="panel" id="roadmap">
  <h3>What has to happen next</h3>
  <p>Ordered by what unblocks what. Each row carries the measurement behind it &mdash; if a row has no number, it does not belong on this list.</p>

  <p><b>Tier 1 &mdash; blocks every accuracy claim on this page.</b> Nothing below this tier can be <em>validated</em> until these land, only made self-consistent.</p>
  <table>
    <tr><th>Item</th><th>Why, with the number</th><th>Cost</th></tr>
    <tr><td><b>Extend the Trackman comparison to CLUB metrics</b></td><td><code>scripts/analysis/compare_trackman.py</code> compares ball speed, club speed, smash, launch V/H, spin and carry. It compares <b>no club data at all</b> &mdash; no face angle, club path, attack angle, dynamic loft or impact location. Until it does, <b>no impact-location or face-angle figure can ever be checked against truth</b>, only against our own internal consistency.</td><td class="t-ok">one session + a day</td></tr>
    <tr><td><b>A target at a tape-known position, visible to both sensors</b></td><td>Camera and radar disagree by about <b>5&deg; in both axes</b> and nothing in the current data can arbitrate. Related: they already agree on the ball to <b>1632 &plusmn; 53 mm vs 1581 mm</b>, so the disagreement is angular, not radial.</td><td class="t-ok">one session</td></tr>
  </table>

  <p><b>Tier 2 &mdash; fixable with data already on disk.</b> No new capture, no new hardware.</p>
  <table>
    <tr><th>Item</th><th>Why, with the number</th><th>Cost</th></tr>
    <tr><td><b>Refit orientation under the radar range ramp</b></td><td>Every published fit renders the clubhead at a fixed <b>1581 mm</b> &mdash; the range of the <em>ball</em>. The radar says the club sweeps <b>~390 mm</b> through that value during the very frames we fit. Observed mask area falls to <b>0.829 &plusmn; 0.222</b> across the window; the ramp predicts <b>0.813</b>, constant range predicts <b>1.000</b>. Constant range is <b>rejected at p &asymp; 0.04</b>. Re-rendering alone is neutral (IoU &minus;0.0035) because orientation was fitted to absorb the error &mdash; it has to be refitted.</td><td class="t-ok">hours</td></tr>
    <tr><td><b>Pin the rotation axis from radar + camera instead of fitting it</b></td><td>Two of the five fit parameters are the angular-velocity axis. Radar range-rate gives the radial component, the camera centroid track gives the two perpendicular ones &mdash; between them that is a full 3D velocity vector, and the axis follows. <b>Drops 2 of 5 free parameters</b> that are currently absorbing noise.</td><td class="t-ok">hours</td></tr>
    <tr><td><b>Open the 22 raw radar captures</b></td><td>Each shot ships a <code>.l3dump</code> (~733 kB) holding the full IWR cube. <code>src/openflight/iwr6843/dump.py</code> already decodes them &mdash; 565 lines, in production use. <b>The silhouette work has never opened one.</b></td><td class="t-ok">hours</td></tr>
    <tr><td><b>Fix <code>detect_face_plane</code></b></td><td>Its extremity gate anchors the mesh frame to the <b>cavity rim on the back of the club</b>, not the face. True loft is 33.10&deg; and lie 61.19&deg;; the detector reported 17.5&deg;. <b>Every dynamic-loft number derived from the mesh inherits this.</b></td><td class="t-ok">hours</td></tr>
    <tr><td><b>Surface the trajectory metrics we already compute</b></td><td><code>ballistics.py</code> returns <code>apex_yards</code>, <code>lateral_yards</code>, <code>landing_angle_deg</code>, <code>landing_speed_mph</code> and a total-distance estimate. <b>None reach the Shot object or the UI.</b> Five Trackman-parity outputs, already computed, currently discarded. No new sensing.</td><td class="t-ok">hours</td></tr>
  </table>

  <p><b>Tier 3 &mdash; needs a new capture or a hardware change.</b></p>
  <table>
    <tr><th>Item</th><th>Why, with the number</th><th>Cost</th></tr>
    <tr><td><b>Capture at 1280&times;800 1:1</b></td><td>Plate scale doubles to <b>0.655 px/mm</b>. Measured on the real mesh: <b>5&deg; of yaw currently changes the silhouette by zero pixels</b>, and 10&deg; buys 2 px &mdash; while a threshold shift also moves the boundary ~1 px. So <b>1 px of segmentation error is worth about 10&deg; of face angle.</b> At 1:1 that 10&deg; becomes 4 px. <span class="t-bad">Caveat: &sect;11j measured real segmented edges improving <b>0.78&times;</b>, not 2&times;, so the optical half is certain and the segmentation half is not.</span></td><td class="t-bad">new capture</td></tr>
    <tr><td><b>A lux and exposure ladder in the actual bay</b></td><td>Decides whether the optical route is a one-degree instrument or a four-degree one. Comparator anchors: <b>Trackman 4 runs 700&ndash;800 lux continuous</b>, <b>Mevo Gen 2 needs only 300 lux</b>. Both are ambient, neither is strobed. We have never measured ours.</td><td class="t-bad">one session</td></tr>
    <tr><td><b>Exact enclosure mounting geometry</b></td><td>The camera is measured level to <b>&minus;0.185&deg; &plusmn; 0.111&deg;</b> and the ball sits <b>163 mm</b> below the lens &mdash; but that was recovered from the footage, not from a drawing. The enclosure the tester used is undocumented, so nothing about mount pitch is reproducible on a second unit.</td><td class="t-bad">drawing + tape</td></tr>
    <tr><td><b>Self-level the camera from the accelerometer</b></td><td><code>inclinometer.py</code> already tilt-compensates the radar from the LIS3DH. Extending it to the camera would make the pitch a <em>measurement</em> rather than an assumption, and would survive an enclosure redesign.</td><td class="t-bad">days</td></tr>
  </table>

  <p><b>Tier 4 &mdash; open questions, not yet tasks.</b> These need a diagnosis before anyone can scope them.</p>
  <table>
    <tr><th>Item</th><th>What is known</th></tr>
    <tr><td><b>Radar club path is rejected on 21 of 21 shots</b></td><td>Status is <code>rejected_phase_span</code> every time. The azimuth phase span runs <b>2.18&ndash;3.91 rad</b> against a <b>&pi;/2</b> ceiling, and the code's own derivation puts the physical expectation near <b>1.3 rad</b> &mdash; so the measured swing is roughly <b>3&ndash;10&times; what the clubhead can actually do</b>. Attack angle comes back pinned near <b>&minus;31&deg; on every shot</b> (sd 2.8&deg;) where a real 7-iron is about &minus;4&deg;: systematic, not a measurement. A likely mechanism is scatterer migration across an extended, rotating head &mdash; 90 mm subtends ~4&deg; at 1.25 m while rotating ~1300&deg;/s &mdash; rather than phase ambiguity. <b>Unconfirmed.</b></td></tr>
    <tr><td><b>Sub-pixel edge segmentation</b></td><td>The masks come from a hard threshold on a background difference. Given that 1 px is worth ~10&deg; of face angle, a sub-pixel edge is worth more than any change to the fit itself. Nobody has tried it.</td></tr>
  </table>

  <p><b>Closed &mdash; do not reopen.</b> <span class="t-bad">Replacing the fit metric.</span> IoU and chamfer disagree on the recovered pose by a median of <b>12.7&deg;</b> (n=18, range 5.8&ndash;28.0&deg;), which is the same scale as the segmentation noise. Neither metric is the problem: the fit is <b>noise-limited, not objective-limited</b>. Two metrics with different failure modes reaching the same answer is a stronger result than either alone.</p>
</div>
"""


def main() -> None:
    page = PAGE.read_text(encoding="utf-8")
    anchor = '<h2><span class="secnum">01</span>'
    if '<div class="panel" id="roadmap">' in page:
        raise SystemExit("roadmap already present -- edit it in place instead")
    assert anchor in page, "section 01 anchor not found"
    assert page.count(anchor) == 1, "section 01 anchor is ambiguous"
    page = page.replace(anchor, ROADMAP.strip() + "\n\n" + anchor, 1)
    PAGE.write_text(page, encoding="utf-8")
    print(f"roadmap inserted into {PAGE}")


if __name__ == "__main__":
    main()
