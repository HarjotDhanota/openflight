"""Build section 11g: why the orientation fails, and whether the shaft fixes it."""

import os
from pathlib import Path

# Where the page fragments are written. Defaults to page/ beside this file;
# override with OPENFLIGHT_PAGE_OUT to point at a live artifact workspace.
_OUT_DIR = Path(
    os.environ.get("OPENFLIGHT_PAGE_OUT", Path(__file__).resolve().parent / "page")
)
_OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT = _OUT_DIR / "sec11g.html"

SEC = """
<h2><span class="secnum">11g</span>Why the orientation is wrong, measured</h2>
<p>&ldquo
The fit cannot get orientation right&rdquo; had been stated on this page several times without ever being quantified. It is a measurable claim, so measure it: take each frame&rsquo;s own best pose, walk <em>one</em> parameter at a time, and watch the objective. A degree of freedom the outline pins shows a sharp peak. One it cannot see shows a plateau &mdash; and on a plateau, the fitted value is whatever noise happens to pick.</p>
<div class="scroll">
<table>
  <thead><tr><th>Degree of freedom</th><th class="num">how far it can move before IoU drops 5&nbsp;%</th><th class="num">IoU remaining at &plusmn;10&deg;</th></tr></thead>
  <tbody>
    <tr><td><b>yaw</b> &mdash; face angle, open/closed</td><td class="num t-bad">&plusmn; 11.3&deg;</td><td class="num t-bad">96.0 %</td></tr>
    <tr><td><b>pitch</b> &mdash; dynamic loft</td><td class="num t-bad">&plusmn; 13.8&deg;</td><td class="num t-bad">96.1 %</td></tr>
    <tr><td><b>roll</b> &mdash; lie / toe-up</td><td class="num t-bad">&plusmn; 10.0&deg;</td><td class="num t-bad">95.1 %</td></tr>
    <tr><td>range &mdash; depth along the ray</td><td class="num t-bad">&plusmn; 138 mm</td><td class="num">94.1 % at &plusmn;100 mm</td></tr>
  </tbody>
</table>
</div>
<p>66 pre-impact frames, each swept around its own optimum. <b>Rotating the clubface by eleven degrees costs four percent of the objective.</b> Frame-to-frame IoU on this data already varies by more than three times that, so the objective genuinely cannot distinguish an eleven-degree face-angle error from measurement noise.</p>
<div class="panel flag">
  <h3>It is not the fitter. It is the cue.</h3>
  <p>No search strategy, smoothness prior or refinement schedule recovers information the objective does not contain. A silhouette of a smooth, roughly wedge-shaped object seen from behind is nearly invariant to the rotations that matter, and this is what the 45&deg
  frame-to-frame pose jumps in &sect;11f actually are: the fit sliding along a flat direction, picking a different point each frame.</p>
  <p>It also explains why constraining the depth improved coherence while lowering IoU. Removing a flat direction removes somewhere for the noise to go. And it explains why IoU is anti-correlated with correctness &mdash
  the last few percent of overlap are bought precisely by moving along the directions the outline cannot see.</p>
</div>

<h3>Would modelling the shaft fix it?</h3>
<p>It is the natural next idea, and a good one: the shaft is long, high-contrast and unambiguous, where the head&rsquo
s outline is small and smooth. A long lever arm should pin rotation far better than a 20&ndash;40&nbsp;px blob.</p>
<p>It does &mdash
but not uniformly, and the pattern matters. Measuring how far the shaft&rsquo;s projected direction moves per degree of each rotation, across a spread of plausible deliveries, with the shaft modelled at a real 900&nbsp;mm rather than the 62&nbsp;mm stub the model carries today:</p>
<div class="scroll">
<table>
  <thead><tr><th>Rotation</th><th class="num">shaft&rsquo;s image direction moves</th><th class="num">face normal moves</th></tr></thead>
  <tbody>
    <tr><td><b>roll</b> &mdash; lie / toe-up</td><td class="num t-ok">0.96&deg; per degree</td><td class="num">0.34&deg; per degree</td></tr>
    <tr><td><b>pitch</b> &mdash; dynamic loft</td><td class="num">0.27&deg; per degree</td><td class="num t-bad">0.99&deg; per degree</td></tr>
    <tr><td><b>yaw</b> &mdash; face angle</td><td class="num">0.19&deg; per degree</td><td class="num t-bad">0.95&deg; per degree</td></tr>
    <tr><td><b>about the shaft&rsquo;s own axis</b></td><td class="num t-bad">0.00&deg; per degree</td><td class="num t-bad">0.84&deg; per degree</td></tr>
  </tbody>
</table>
</div>
<p>Read the two columns against each other. <b>The rotations the shaft sees best are the ones that move the clubface least, and vice versa.</b> The shaft pins lie almost perfectly and is weakest on exactly the two quantities impact location needs. And rotation about its own axis it cannot see at all, by construction &mdash
while that rotation swings the face normal on a cone of half-angle 56.8&deg;, which is to say it <em>is</em> face angle and loft.</p>
<div class="panel ok">
  <h3>Worth doing anyway, and here is the honest reason</h3>
  <p>0.19&deg
  per degree sounds fatal until you ask how precisely the shaft&rsquo;s direction can be measured. It is a long, straight, high-contrast line across many pixels, so about half a degree is realistic &mdash; which puts face angle inside roughly <b>&plusmn;3&deg;</b>, against the <b>&plusmn;11&deg;</b> the head&rsquo;s outline manages today. Weak leverage on a precisely measured quantity still beats strong leverage on an imprecise one.</p>
  <p>The better argument is structural. The shaft pins two rotational degrees of freedom hard, which turns a badly-conditioned three-dimensional search into a one-dimensional one along a known axis. The head&rsquo
  s outline only has to resolve the remainder, and a weak signal in one dimension is a far easier problem than a weak signal in three.</p>
  <p>The model already contains a 62&nbsp
  mm shaft stub, which is why it renders one; a real shaft is fourteen times longer and would carry fourteen times the leverage.</p>
</div>

<h3>The toe tip is a trap</h3>
<p>The companion idea &mdash
find the far end of the club and match it to the model&rsquo;s toe &mdash; has a specific flaw that is easy to miss. <b>The outermost point of a silhouette is not a fixed point on the object.</b> It is wherever the surface happens to turn away from the camera, and as the club rotates that contact slides across the metal. Matching it to a fixed model vertex therefore injects an error that grows with the very rotation being solved for.</p>
<p>This is not new: the June keypoint design spec excludes silhouette-tangent extrema for exactly this reason, and its stated key risk was that the behind-ball view offers only a crown-and-hosel cluster with no keypoints on the face. The measurements above are that prediction coming true on real data.</p>
<p>The hosel has been tried directly and measured <b>worst of four</b> candidate reference points &mdash
4.90&nbsp;px against 1.71&nbsp;px for a plain blob centroid &mdash; because the neck is the lowest-contrast part of the club. The distinction worth holding onto is that the shaft&rsquo;s <em>direction</em> is stable while its <em>endpoint</em> is not, so the usable cue is the line, never the point where it meets the head.</p>
"""

OUT.write_text(SEC, encoding="utf-8")
print(f"wrote {OUT}")
