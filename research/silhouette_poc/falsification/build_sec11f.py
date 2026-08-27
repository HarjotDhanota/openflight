"""Build the section 11f fragment with the fit overlays embedded."""

from pathlib import Path

HERE = Path(__file__).parent
import os

# Where the page fragments are written. Defaults to page/ beside this file;
# override with OPENFLIGHT_PAGE_OUT to point at a live artifact workspace.
_OUT_DIR = Path(
    os.environ.get("OPENFLIGHT_PAGE_OUT", Path(__file__).resolve().parent / "page")
)
_OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT = _OUT_DIR / "sec11f.html"
b64 = dict(
    line.split("||", 1)
    for line in (HERE / "renders/_fit_b64.txt").read_text(encoding="utf-8").split("\n")
)

SEC = """
<h2><span class="secnum">11f</span>Actually looking at the mesh fit</h2>
<p>&sect
11e found the mesh fitter&rsquo;s depth search pinned to a camera-to-ball range of 1425&nbsp;mm when the rig measures 1581&nbsp;mm. The obvious question is whether that mattered, and the obvious way to answer it is to fit every silhouette under both grids and compare. That produces a table. A table is not a picture of a fit, and this page had been reporting fit quality for a long time without ever showing one.</p>
<p>So here is one. The outline is the boundary of the model&rsquo
s own rasterised projection at the fitted pose &mdash; the same render the objective scores. Nothing padded, nothing hand-drawn.</p>

<div class="panel flag">
  <h3>The first render found a bug the numbers had hidden</h3>
  <p>Drawing the first attempt showed the tracked outline sitting on a small round object in the top corner while <b>the clubhead was plainly visible at the bottom of the frame</b>. The tracker had followed the departing <em>ball</em> after impact &mdash
  the exact failure the shipped tracker&rsquo;s own comments warn about, reintroduced.</p>
  <p>Those ball frames carried the <b>highest IoU in the set</b>, 0.56&ndash
  0.62, because a small round blob is easy to cover with anything. They were also the most temporally consistent, because a ball flies smoothly. <b>The contamination flattered every metric being used to judge the fit</b>, and the numbers alone gave no hint. One image did.</p>
  <p>The tracker now stops at contact &mdash
  using the ball&rsquo;s own image track extrapolated back to the tee row, per shot &mdash; and vetoes the ball explicitly. Everything below is the corrected run: <b>66 pre-impact frames across 21 shots</b>, which are the frames club delivery is defined on anyway.</p>
</div>

<figure>
<img alt="Nine panels in three rows. Each shows a zoomed camera crop of an iron approaching a golf ball. A cyan outline marks the observed silhouette and an orange outline the fitted mesh. The three rows use different depth treatments." src="data:image/jpeg;base64,__STRIP__">
<figcaption><b>One shot, three frames before impact, fitted three ways.</b> Cyan is the observed silhouette the fit consumed
orange is the model&rsquo;s own projection at the fitted pose. Rows: the shipped depth grid, the corrected grid, and range pinned at the measured 1581&nbsp;mm. Reading across a row shows how coherent the pose sequence is; reading down a column shows what the depth treatment changed. The ball is the pale disc at bottom centre.</figcaption>
</figure>

<div class="scroll">
<table>
  <thead><tr><th></th><th class="num">A &mdash; shipped grid</th><th class="num">B &mdash; corrected grid</th><th class="num">C &mdash; range pinned</th></tr></thead>
  <tbody>
    <tr><td>depth treatment</td><td class="num">search 1300&ndash;1550</td><td class="num">search 1456&ndash;1706</td><td class="num">fixed at 1581</td></tr>
    <tr><td>frames fitted</td><td class="num">66 / 66</td><td class="num">66 / 66</td><td class="num">66 / 66</td></tr>
    <tr><td><b>median IoU</b></td><td class="num">0.4625</td><td class="num">0.4401</td><td class="num t-bad">0.3896</td></tr>
    <tr><td>median fitted range</td><td class="num t-bad">1180 mm</td><td class="num t-bad">1336 mm</td><td class="num t-ok">1581 mm</td></tr>
    <tr><td>error against the tape</td><td class="num t-bad">&minus;401 mm</td><td class="num t-bad">&minus;245 mm</td><td class="num t-ok">0</td></tr>
    <tr><td>settling below their own grid</td><td class="num t-bad">78.8 %</td><td class="num t-bad">81.8 %</td><td class="num">&mdash;</td></tr>
    <tr><td>railed on the refinement limit</td><td class="num">18.2 %</td><td class="num">25.8 %</td><td class="num">&mdash;</td></tr>
    <tr><td><b>median pose jump between frames</b></td><td class="num t-bad">44.68&deg;</td><td class="num">37.89&deg;</td><td class="num t-ok">31.83&deg;</td></tr>
    <tr><td><b>adjacent pairs jumping &gt; 45&deg;</b></td><td class="num t-bad">50.0 %</td><td class="num">44.4 %</td><td class="num t-ok">33.3 %</td></tr>
  </tbody>
</table>
</div>

<h3>Overlap and correctness move in opposite directions</h3>
<p>Constrain the depth harder and silhouette overlap gets steadily <em>worse</em> &mdash
0.4625, 0.4401, 0.3896 &mdash; while pose coherence gets steadily <em>better</em>: 50&nbsp;%, 44&nbsp;%, 33&nbsp;% of adjacent frames jumping more than 45&deg;. A clubhead does not reorient by 45&deg; in 2.1&nbsp;ms, so those jumps are the fit choosing between explanations the objective cannot separate, and the extra depth freedom is what lets it do so.</p>
<div class="panel flag">
  <h3>IoU is not a progress metric here</h3>
  <p>Across three arms differing <em>only</em> in how depth is treated, <b>the arm with the best IoU has the worst poses and the arm with the worst IoU has the best poses.</b> Silhouette overlap is not a proxy for correctness on this problem
  over the range that matters it is an inverse one.</p>
  <p>The pattern held before the ball contamination was removed and after it, with every absolute number changing in between. That is what makes it a finding rather than an artefact &mdash
  and it means every decision made by maximising IoU, including reading a rising IoU as progress, was tuned against the goal.</p>
</div>

<h3>Why the fit runs away from the true depth</h3>
<p>The most striking column is not the IoU, it is the fitted range. Under the shipped grid the fit settles <b>401&nbsp
mm short</b> of where the club physically is; under the corrected grid, still 245&nbsp;mm short. Around 80&nbsp;% of frames end up below the grid they were searched in, and up to a quarter hit the maximum excursion the refinement allows and are cut off still moving. Recentring the grid does not stop it, because the grid was never the cause.</p>
<figure>
<img alt="Three panels showing the observed clubhead silhouette in cyan against the fitted mesh outline in orange, rendered at the measured range. The orange outline is visibly smaller than the cyan one and does not cover a thin tail extending from it." src="data:image/jpeg;base64,__SCALE__">
<figcaption><b>The mismatch, at the range the tape says.</b> The model covers only <b>43&ndash
55&nbsp;%</b> of the observed pixels. A shorter range makes the projection larger, so the fit was pulling the club nearer to close that gap. Note the thin cyan tail running up and left out of each silhouette: that is shaft and hosel, which a head-only model cannot cover at any range.</figcaption>
</figure>
<p>That gives the diagnosis a shape. The observation is roughly twice the model&rsquo
s area, and a visible part of the excess is shaft the model does not contain &mdash; the head/shaft separation is leaving neck pixels in the head partition. Motion blur adds more: the head travels about 9&nbsp;mm during the exposure. Whether those two account for all of it, or whether the model is genuinely smaller than the real clubhead, is the next thing to measure. <b>Pinning the range does not fix this, it hides it</b>, and the drop from 0.46 to 0.39 is the size of what is being hidden.</p>

<div class="panel warn">
  <h3>What to take from this</h3>
  <p><b>Pin the range from the radar.</b> It costs 0.07 of a metric that points the wrong way and cuts the impossible-pose rate by a third. Handoff &sect
  6 has listed this as the cheapest route to a genuinely 3D fit for some time; this is the measurement that justifies it.</p>
  <p><b>Stop reporting IoU as fit quality.</b> Report pose coherence and range agreement instead. Both are available today and both point the right way.</p>
  <p><b>Do not read these against the older numbers.</b> The 349 frames at IoU 0.633 quoted earlier came from a tracker that no longer exists and cannot be reproduced. Only the comparison between arms is valid, because all three see byte-identical silhouettes.</p>
  <p>And a third of adjacent poses still jump more than 45&deg
  with depth pinned. Removing one degenerate degree of freedom is not the same as solving pose.</p>
</div>
"""

OUT.write_text(
    SEC.replace("__STRIP__", b64["strip_shot_029_9-iron"]).replace(
        "__SCALE__", b64["scale_shot_029_9-iron"]
    ),
    encoding="utf-8",
)
print(f"wrote {OUT}")
