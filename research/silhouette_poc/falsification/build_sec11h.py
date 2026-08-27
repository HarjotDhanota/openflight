"""Build section 11h and the 11f/11g corrections."""

import os
from pathlib import Path

# Where the page fragments are written. Defaults to page/ beside this file;
# override with OPENFLIGHT_PAGE_OUT to point at a live artifact workspace.
_OUT_DIR = Path(
    os.environ.get("OPENFLIGHT_PAGE_OUT", Path(__file__).resolve().parent / "page")
)
_OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT = _OUT_DIR / "sec11h.html"

SEC = """
<h2><span class="secnum">11h</span>Three routes to face angle, measured against each other</h2>
<div class="panel flag">
  <h3>Correction: the model has no shaft at all</h3>
  <p>This page called the model&rsquo
  s 62&nbsp;mm protrusion a shaft stub. It is not. Measured along its own axis it runs <b>63.8&nbsp;mm at 12.9&ndash;17.5&nbsp;mm diameter</b>, which is a <b>hosel and ferrule</b> &mdash; an iron shaft tip is 9&ndash;10&nbsp;mm across and about 900&nbsp;mm long.</p>
  <p>So the earlier statement was closer to right than the correction that replaced it: <b>the model contains head, hosel and ferrule, and no shaft whatsoever.</b> The long tail in the observed silhouette is real shaft, and the model cannot cover it at any range. Two wrong answers in a row here, both from reading a dimension off a bounding box instead of measuring the part.</p>
</div>

<h3>The foreshortening idea, measured</h3>
<p>The proposal: find the heel and the toe, measure the distance between them in the image, and compare it against the same distance on a square face. A rotated face projects shorter, so the shortening should give the rotation &mdash
and the same trick should work between topline and sole.</p>
<p>The physics is real. Measuring it on the model&rsquo
s own projection, using the silhouette&rsquo;s second-moment ellipse rather than two hand-picked landmarks:</p>
<div class="scroll">
<table>
  <thead><tr><th>Rotation</th><th class="num">major axis</th><th class="num">aspect ratio</th><th class="num">ellipse orientation</th></tr></thead>
  <tbody>
    <tr><td><b>yaw</b> &mdash; face angle</td><td class="num t-bad">0.089 px/deg</td><td class="num">0.28 %/deg</td><td class="num">0.11&deg;/deg</td></tr>
    <tr><td><b>pitch</b> &mdash; dynamic loft</td><td class="num t-bad">0.053 px/deg</td><td class="num">0.31 %/deg</td><td class="num">0.17&deg;/deg</td></tr>
    <tr><td><b>roll</b> &mdash; lie / toe-up</td><td class="num">0.022 px/deg</td><td class="num">0.09 %/deg</td><td class="num t-ok">1.00&deg;/deg</td></tr>
  </tbody>
</table>
</div>
<p>The head spans <b>23.6&nbsp
px</b> heel to toe at this plate scale, and segmentation on this footage localises an edge to roughly half a pixel. At <b>0.089&nbsp;px per degree</b>, resolving one pixel of shortening takes <b>eleven degrees</b> of face rotation &mdash; so the method lands at <b>6&ndash;11&deg;</b> of yaw resolution.</p>
<div class="panel">
  <h3>Two independent routes hitting the same wall is the finding</h3>
  <p>That 6&ndash
  11&deg; is the <em>same</em> number the IoU sweep produced from a completely different measurement: &plusmn;11.3&deg; before the objective drops five percent. Foreshortening geometry and silhouette overlap agree because they are two views of one fact &mdash; a projected length goes as <code>cos</code> of the rotation, and <b>cosine has zero slope at zero</b>. Square is exactly where golf shots live, and it is exactly where the cue has no sensitivity. A ten-degree open face shortens the head by 1.5&nbsp;%: about a third of a pixel.</p>
  <p>There is a sign problem too &mdash
  <code>cos</code> is even, so an open face and a closed face foreshorten identically.</p>
  <p>But look at the last column. <b>Ellipse orientation tracks roll one-for-one.</b> That is the third independent measurement agreeing that this vantage determines <em>lie</em> well and face angle badly, after the IoU sweep and the shaft-leverage calculation. Three routes, one answer: the information about face angle is not in this image.</p>
</div>

<h3>So use the ball instead &mdash
and the numbers strongly favour it</h3>
<p>The other suggestion was to take face angle from where the ball actually starts. That has real merit and it is what every comparator does. The ball leaves along a weighted blend of face angle and club path, and this project already inverts it at a 69/31 horizontal split:</p>
<pre><code>face angle = (ball direction &minus
0.31 &times; club path) / 0.69</code></pre>
<p>What makes this attractive is that the camera measures ball direction <em>far</em> better than it measures the clubface. Two independently written implementations of the ball track agree to <b>0.199&deg
</b>. Propagating that:</p>
<div class="scroll">
<table>
  <thead><tr><th>Route to face angle</th><th class="num">resolution</th></tr></thead>
  <tbody>
    <tr><td>silhouette fit</td><td class="num t-bad">&plusmn; 11.3&deg;</td></tr>
    <tr><td>heel&ndash;toe foreshortening</td><td class="num t-bad">6&ndash;11&deg;</td></tr>
    <tr><td>ball direction, with club path known to 5&deg;</td><td class="num">2.26&deg;</td></tr>
    <tr><td>ball direction, with club path known to 2&deg;</td><td class="num t-ok">0.94&deg;</td></tr>
    <tr><td>ball direction, with club path known exactly</td><td class="num t-ok">0.29&deg;</td></tr>
  </tbody>
</table>
</div>
<p><b>Even a five-degree club-path error beats the silhouette by a factor of five.</b> The optical route is trying to read a quantity the image barely encodes
the ball route reads a quantity the image encodes extremely well and converts it with a known coefficient. That is the right way round.</p>
<div class="panel warn">
  <h3>Two things stand between this and a working face angle</h3>
  <p><b>Club path.</b> The formula needs it, and the radar&rsquo
  s club path is currently rejected on <b>21 of 21</b> shots. Nothing else in the system supplies one. This is the unlock, and it is the same channel whose attack angle is also rejected 21/21.</p>
  <p><b>Absolute yaw calibration.</b> The 0.199&deg
  above is <em>agreement between two implementations</em>, which is precision, not accuracy. Face angle is measured against the target line, so a constant camera-yaw error passes into it at 1.45&times; &mdash; and the camera and radar currently disagree about horizontal direction by about 5&deg; (&sect;11c). That single unresolved offset would put a 7&deg; bias into every face angle. It is the same measurement &sect;11d asks for: one target at a tape-known position, visible to both sensors.</p>
</div>

<h3>Face angle is not impact location, though</h3>
<p>Worth being clear about the last step, because it is the one that keeps getting skipped. Face angle tells you where the face pointed
it says nothing about <em>where on the face</em> the ball struck. Getting toe&ndash;heel and high&ndash;low from ball data alone means reading the strike back out of the collision, and there are only three signals available:</p>
<div class="scroll">
<table>
  <thead><tr><th>Signal</th><th>What it gives</th><th>On an iron</th></tr></thead>
  <tbody>
    <tr><td>ball speed loss (smash factor)</td><td>roughly how far off centre &mdash; a distance, not a direction</td><td class="num">weak, and confounded by strike quality and turf</td></tr>
    <tr><td>gear effect on spin axis</td><td>the direction of the miss</td><td class="num t-bad">an iron&rsquo;s centre of gravity sits ~5&nbsp;mm behind the face against ~35&nbsp;mm on a driver, so the effect is a fraction of the size &mdash; and this project does not yet measure spin at all</td></tr>
    <tr><td>launch angle against dynamic loft</td><td>high or low on the face</td><td class="num">needs a trustworthy dynamic loft, which needs attack angle &mdash; rejected 21/21</td></tr>
  </tbody>
</table>
</div>
<p>So the ball route leads to a good face angle, a coarse radial miss, and a direction that irons largely refuse to give up. That is a real product &mdash
face angle, path, and a centre/toe/heel/high/low <em>zone</em> &mdash; but it is not the millimetric strike map, and it should be scoped as the former rather than promised as the latter. The June design spec reached the same fork from simulation; this is the same fork reached from measurement.</p>
"""

OUT.write_text(SEC, encoding="utf-8")
print(f"wrote {OUT}")
