"""Build and insert the Codex follow-up as section 11j of the local artifact.

The cloud Claude artifact requires an authenticated Claude session, which this
environment cannot mutate.  This updates the latest local artifact source and
writes a standalone section for paste/upload.  Every replacement asserts both
presence and uniqueness before writing.
"""

from __future__ import annotations

import os
from pathlib import Path

# Live artifact workspace. Defaults to page/ beside this file; set
# OPENFLIGHT_PAGE_OUT to the directory holding openflight-impact-location.html
# when re-publishing.
SCRATCHPAD = Path(
    os.environ.get("OPENFLIGHT_PAGE_OUT", Path(__file__).resolve().parent / "page")
)
SCRATCHPAD.mkdir(parents=True, exist_ok=True)
PAGE = SCRATCHPAD / "openflight-impact-location.html"
OUT = SCRATCHPAD / "sec11j.html"
START = "<!-- CODEX-11J-START -->"
END = "<!-- CODEX-11J-END -->"
SECTION_12 = '<h2><span class="secnum">12</span>Contribute a capture</h2>'

SECTION = f"""
{START}
<h2><span class="secnum">11j</span>The physical validators were run</h2>
<div class="panel flag">
  <h3>The club angles are not measurements yet</h3>
  <p>The frozen 21-shot replay now identifies a deterministic reason club path is rejected. The accepted path voltage-averages TX1 and TX3 before taking phase. As their elevation phases separate, the reference approaches cancellation and flips by 2&ndash
  3 radians. The separate-reference midpoint already in the code cuts the median largest step from <b>2.73 to 0.34 rad</b>, but its path remains unvalidated.</p>
</div>
<div class="scroll"><table>
  <thead><tr><th>Full-set falsification</th><th class="num">result</th><th>verdict</th></tr></thead>
  <tbody>
    <tr><td>AoA delete-one-frame jackknife</td><td class="num t-bad">6.4&deg; median range; 21/21 &gt;2&deg;</td><td>four/five frames do not support the slope</td></tr>
    <tr><td>impact-time perturbation</td><td class="num t-bad">AoA 5.1&deg;; path 16.5&deg;</td><td>contact-time uncertainty changes the answer qualitatively</td></tr>
    <tr><td>same-point radial check</td><td class="num">+0.3 m/s median; 1.1 m/s MAD</td><td>weak necessary check passes on 19 shots, but shares the OPS prior</td></tr>
    <tr><td>LCMF geometry envelope</td><td class="num t-bad">tilt &plusmn;2&deg; &rarr; about &plusmn;4.1&deg;</td><td>absolute launch uncertainty is calibration-dominated</td></tr>
    <tr><td>LCMF component club gap</td><td class="num">2.99&deg; / 3.09&deg;</td><td>the relative gap exists in both saved component models</td></tr>
  </tbody>
</table></div>

<h3>The silhouette mismatch was decomposed</h3>
<p>At the tape range, observed area is <b>1.939&times
</b> the sharp CAD render. The CAD contains a 61.8 mm hosel/ferrule, not a shaft. Re-splitting at its projected reach removes a median <b>21.4%</b> of the observed mask. Integrating the exact CAD render over the recorded exposure adds <b>28.3%</b> area. After both, the median remaining area ratio is <b>1.223</b>, equivalent to 1.106&times; linear scale&mdash;but the broad IQR crosses 1.0, pose is unvalidated, and a 7-iron CAD is being used on 9-iron footage. Measure the heads before scaling the mesh.</p>

<h3>The sequence models do not recover pose</h3>
<p>The five-parameter rigid model now fixes <code>|omega|=v/r</code> at 1,124&ndash
1,580&deg;/s. It gives up about 0.041 IoU, but the fitted axis changes by as much as 82&deg; under a modest radius sweep. The old <code>fit_sequence</code> is worse physically: it removes pose jumps by freezing motion at a median 0&ndash;1&deg; per frame pair and pulls range to 1,175/1,256 mm in arms A/B. Coherence created by a &ldquo;do not move&rdquo; penalty is not swing evidence.</p>

<div class="panel warn">
  <h3>Correction to &sect
  11i: the lens extrapolation did not survive real edges</h3>
  <p>Halving 65 real segmented masks narrowed the yaw basin from 11.25&deg
  to 8.75&deg; instead of doubling it: ratio <b>0.78</b> versus the assumed 2.00. Downsampling also denoises and reshapes the mask, so this does not prove lower resolution is better. It does prove that clean-render leverage does not scale linearly through the current real segmentation pipeline. The &ldquo;6 mm lens gives about 1&deg;&rdquo; row is now an <b>unvalidated projection</b>, not a recommendation. Test a real 1:1 exposure/lux/blur ladder first; ambient light and no strobe remain the constraints.</p>
</div>

<h3>What to do next</h3>
<ol>
  <li>Log per-frame TX1, TX3, separate midpoint, range bin, weight and target identity
  validate the separate-reference path as a frozen arm without widening the existing reject gate.</li>
  <li>Use only pre-impact sensor timestamps and estimate contact inside an uncertainty interval.</li>
  <li>Collect a common camera/radar rigid target, then a blind path/AoA and impact-tape reference session.</li>
  <li>Fit the ball with a known-radius, missing-sector gradient/photometric sphere model
  do not calibrate focal length from its biased threshold contour.</li>
  <li>Keep face angle experimental and impact location withheld until a registered face origin/axes and validated rigid head state exist.</li>
</ol>
<p>The complete tables, caveats and replay files are in <code>docs/superpowers/specs/2026-08-26-codex-followup-results.md</code>.</p>
{END}
"""


def main() -> None:
    source = PAGE.read_text(encoding="utf-8")
    if source.count(START) == 1 and source.count(END) == 1:
        before, remainder = source.split(START, 1)
        _old, after = remainder.split(END, 1)
        updated = before + SECTION + after
    else:
        if source.count(START) != 0 or source.count(END) != 0:
            raise RuntimeError("section 11j markers are incomplete or duplicated")
        if source.count(SECTION_12) != 1:
            raise RuntimeError(
                f"section 12 insertion marker count is {source.count(SECTION_12)}, expected 1"
            )
        updated = source.replace(SECTION_12, SECTION + "\n" + SECTION_12, 1)
    OUT.write_text(SECTION, encoding="utf-8")
    PAGE.write_text(updated, encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"updated {PAGE}")


if __name__ == "__main__":
    main()
