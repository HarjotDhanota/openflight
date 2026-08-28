"""Bring the report's Recommendations up to date with what was completed.

Four of its rows were executed on 2026-08-27 and one was half-executed, and
the outcomes changed what the remaining items are worth -- so completed items
are kept with their results rather than deleted, and the segmentation item is
re-scoped to the measured, bounded gain (~2x, not open-ended).
"""

from __future__ import annotations

import re
from pathlib import Path

p = Path("research/silhouette_poc/falsification/build_report.py")
s = p.read_text(encoding="utf-8")

m = re.search(r"def _recommendations\(\) -> str:.*?(?=\ndef _reproduce)", s, re.S)
assert m, "recommendations function not found"

NEW = '''def _recommendations() -> str:
    return f"""
{heading("recommendations", OUTLINE)}
<p>Ordered by dependency. Each item carries the measurement that justifies it. Items completed since this report was first issued are kept, with their outcomes, because several outcomes changed what the remaining items are worth.</p>

<h3 style="margin-top:26px">Completed, 2026-08-27 &mdash; and what each one showed</h3>
<div class="scroll"><table>
  <tr><th>Item</th><th>Outcome</th></tr>
  <tr><td><b>Extend the Trackman comparison to club metrics</b></td><td class="t-ok">Shipped.</td></tr>
  <tr><td colspan="2"><code>compare_trackman.py</code> now compares twelve club delivery metrics &mdash; attack angle, club path, face angle, face-to-path, dynamic loft, spin loft, swing plane and direction, spin axis, impact height and offset, low point &mdash; and its summary names the metrics OpenFlight cannot yet produce rather than omitting them. Only pipeline-accepted values are read, never rejected candidates. <b>The harness is ready; it now needs a session alongside a Trackman.</b></td></tr>
  <tr><td><b>Impact timing from the installation</b></td><td class="t-ok">Shipped.</td></tr>
  <tr><td colspan="2"><code>src/openflight/acoustic.py</code>: contact = trigger &minus; distance &divide; speed of sound. Model and measured ball departure agree to <b>0.04 frames</b>. This also corrected a 3.89-frame error that had anchored the research fits (&sect;03).</td></tr>
  <tr><td><b>Surface computed trajectory metrics</b></td><td class="t-ok">Shipped.</td></tr>
  <tr><td colspan="2">Apex, lateral deviation, flight time, landing speed, landing angle and total distance now reach the Shot object and the UI payload. Five Trackman-parity outputs that were being computed and discarded.</td></tr>
  <tr><td><b>Radar range model + rotation-axis constraint</b></td><td class="t-warn">Implemented; orientation still fails.</td></tr>
  <tr><td colspan="2">Range now comes from the radar's own range rate and the rotation axis from the fused velocity, dropping the fit from five free parameters to four. The velocity half <b>validates</b>: fused |v| matches the OPS243's independent club speed at <b>0.97&ndash;1.00&nbsp;&plusmn;&nbsp;0.03</b>. The orientation half does not: <b>0 of 6</b> shots land inside the physical envelope, and refitting with the corrected impact anchor moved the recovered angles substantially &mdash; a fit that sensitive to its time anchor is not extracting orientation from the pixels. <b>This is why the remaining items are ordered as they are.</b></td></tr>
  <tr><td><b>Analyse the raw radar captures</b></td><td class="t-warn">Opened; rotation unconfirmed.</td></tr>
  <tr><td colspan="2">All 22 <code>.l3dump</code> files decoded. The clubhead's Doppler width (1.95 bins median) exceeds the point-target floor (1.27) by close to the rotation-predicted amount, but the discriminating test has no statistical power on a 7-iron/9-iron-only session. Needs the wide-speed-range capture below.</td></tr>
</table></div>

<h3>Tier 1 &mdash; prerequisite for any accuracy claim</h3>
<div class="scroll"><table>
  <tr><th>Item</th><th>Justification</th><th class="num">Cost</th></tr>
  <tr><td><b>A session alongside a Trackman, using the new club-metric comparison</b></td><td>The harness exists; no club figure can be validated until it has truth to compare against.</td><td class="num">1 session</td></tr>
  <tr><td><b>Target at a taped position, visible to both sensors</b></td><td>Resolves the <b>5&deg;</b> camera/radar disagreement, which nothing in the current data can arbitrate.</td><td class="num">1 session</td></tr>
</table></div>

<h3>Tier 2 &mdash; actionable with existing data</h3>
<div class="scroll"><table>
  <tr><th>Item</th><th>Justification</th><th class="num">Cost</th></tr>
  <tr><td><b>Extract more of the frames the club already appears in</b></td><td>The club is visible for roughly <b>ten frames</b> before contact (about f62&ndash;f72 at this framing); the current extractor keeps <b>3&ndash;5</b>, losing the early frames against the dark netting. A better segmenter therefore roughly <b>doubles</b> the observations per shot &mdash; a bounded gain, not an open-ended one, since nothing recovers more frames than the club is in view for. Against four fit parameters, 5&rarr;10 observations changes the conditioning materially.</td><td class="num">days</td></tr>
  <tr><td><b>Correct <code>detect_face_plane</code></b></td><td>Still anchors the model frame to the cavity rim on the reverse of the club (true loft <b>33.10&deg;</b>, reported 17.5&deg;). Interim workaround exists: <code>replay/club_angles.py</code> carries the measured axes, and its <code>square_pose()</code> is now the required seed for any fit &mdash; the mesh frame's origin is a <em>backwards</em> club.</td><td class="num">hours</td></tr>
</table></div>

<h3>Tier 3 &mdash; requires a new capture or hardware change</h3>
<div class="scroll"><table>
  <tr><th>Item</th><th>Justification</th><th class="num">Cost</th></tr>
  <tr><td><b>Capture at 1280&times;800, 1:1</b></td><td>Plate scale doubles to <b>0.655 px/mm</b>, so 10&deg; of face angle becomes 4 px rather than 2, at the same frame rate and field of view. Multiplies with the segmentation item above &mdash; same frames, more pixels each. The optical half is certain; whether segmentation error stays near 1 px is not &mdash; earlier testing on real segmented edges gave a 0.78&times; improvement, not 2&times;.</td><td class="num">1 session</td></tr>
  <tr><td><b>Capture across a wide club-speed range</b></td><td>Every shot here is a 7-iron or 9-iron with no speed overlap, which left the radar-rotation test unable to discriminate. A driver and a wedge in one session removes the confound and would settle whether the Doppler broadening is rotation.</td><td class="num">1 session</td></tr>
  <tr><td><b>Lux and exposure ladder in the bay</b></td><td>Determines whether the optical route is a one-degree or four-degree instrument. Comparator anchors: Trackman 4 at 700&ndash;800 lux, Mevo Gen 2 at 300 lux, both continuous.</td><td class="num">1 session</td></tr>
  <tr><td><b>Dimension the enclosure; self-level the camera</b></td><td>Camera pitch is currently recovered from footage rather than specified, so it is not reproducible on a second unit. <code>inclinometer.py</code> already tilt-compensates the radar. The acoustic timing fix also depends on a per-installation ball-to-unit distance, which belongs in the same calibration record.</td><td class="num">days</td></tr>
</table></div>

<h3>Candidate approaches, not yet evaluated</h3>
<div class="scroll"><table>
  <tr><th>Approach</th><th>Rationale</th><th>Principal risk</th></tr>
  <tr><td><b>Sub-pixel edge extraction</b></td><td>Masks come from a hard threshold. Given that one pixel is worth ~10&deg; of face angle, boundary precision is worth more than any change to the fit.</td><td>Motion blur may already exceed sub-pixel scale &mdash; the club moves about 3 px during exposure.</td></tr>
  <tr><td><b>Mark the club on the measuring rig only</b></td><td>The shipped product must be markerless; a calibration rig need not be. Provides per-frame truth to score the markerless estimator against.</td><td>None technical. Requires the rig to be built.</td></tr>
  <tr><td><b>Second camera</b></td><td>Stereo resolves depth directly and would settle the range question outright.</td><td>Cost and synchronisation; does not address the segmentation limit, which is currently binding.</td></tr>
</table></div>
"""


'''

s = s[: m.start()] + NEW + s[m.end() :]

old = (
    "<p>The highest-value next step is not another estimator. It is <b>extending the "
    "Trackman comparison to club metrics</b>, since no club figure can be validated until "
    'it exists. Full priorities in <a href="#recommendations">Recommendations</a>.</p>'
)
assert s.count(old) == 1, "summary next-step line not found"
s = s.replace(
    old,
    "<p>The highest-value next step is not another estimator. The club-metric comparison "
    "harness has now been <b>built and shipped</b>; the step is a <b>session alongside a "
    "Trackman</b> to give it truth to compare against. Full priorities, including what was "
    "completed on 2026-08-27 and what each completion showed, in "
    '<a href="#recommendations">Recommendations</a>.</p>',
    1,
)

p.write_text(s, encoding="utf-8")
print("recommendations rewritten, summary updated")
