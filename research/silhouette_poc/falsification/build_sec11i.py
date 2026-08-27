"""Build section 11i: is resolution really the constraint?"""

import os
from pathlib import Path

# Where the page fragments are written. Defaults to page/ beside this file;
# override with OPENFLIGHT_PAGE_OUT to point at a live artifact workspace.
_OUT_DIR = Path(
    os.environ.get("OPENFLIGHT_PAGE_OUT", Path(__file__).resolve().parent / "page")
)
_OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT = _OUT_DIR / "sec11i.html"

SEC = """
<h2><span class="secnum">11i</span>&ldquo;We just need more resolution&rdquo; &mdash; tested</h2>
<p>Every route on this page has ended at the same place, and the natural conclusion is that the camera simply does not have enough pixels. That is a claim with a number attached, so here is the number.</p>
<p>First, a correction to &sect
11h. The argument there was that foreshortening sensitivity vanishes at square because cosine has zero slope at zero. Measured, it does not vanish &mdash; it weakens by about a factor of four, because a clubhead is a solid body rather than a flat plate, so rotating it changes the outline through more than the shortening of one line:</p>
<div class="scroll">
<table>
  <thead><tr><th class="num">face angle (deg open)</th><th class="num">0</th><th class="num">2</th><th class="num">5</th><th class="num">10</th><th class="num">20</th><th class="num">30</th></tr></thead>
  <tbody>
    <tr><td>leverage, px per degree</td><td class="num">0.059</td><td class="num">0.074</td><td class="num">0.113</td><td class="num">0.154</td><td class="num">0.199</td><td class="num">0.228</td></tr>
    <tr><td>face angle resolution</td><td class="num t-bad">8.5&deg;</td><td class="num t-bad">6.8&deg;</td><td class="num">4.4&deg;</td><td class="num">3.3&deg;</td><td class="num">2.5&deg;</td><td class="num">2.2&deg;</td></tr>
  </tbody>
</table>
</div>
<p>Real shots live in the first two columns, which is the weakest part of the curve &mdash
but the cue is not dead there, it is just four times worse than at a wide-open face. That matters, because a signal that is merely weak <em>can</em> be rescued by pixels, where one that is truly zero cannot.</p>

<h3>The requirement</h3>
<p>Leverage scales linearly with plate scale, so the requirement can be read straight off. At a half-pixel edge-localisation floor, measured at a five-degree open face:</p>
<div class="scroll">
<table>
  <thead><tr><th>Configuration</th><th class="num">plate scale</th><th class="num">head</th><th class="num">face angle</th><th class="num">field of view</th></tr></thead>
  <tbody>
    <tr><td>today &mdash; 320&times;200, 2&times; subsampled</td><td class="num">0.295 px/mm</td><td class="num">23.6 px</td><td class="num t-bad">4.41&deg;</td><td class="num">2.17 m</td></tr>
    <tr><td>1:1 readout, same 2.8 mm lens</td><td class="num">0.590 px/mm</td><td class="num">47.2 px</td><td class="num">2.21&deg;</td><td class="num">2.17 m</td></tr>
    <tr><td><b>1:1 readout, 6 mm lens</b></td><td class="num">1.265 px/mm</td><td class="num">101 px</td><td class="num t-ok">1.03&deg;</td><td class="num">1.01 m</td></tr>
    <tr><td>1:1 readout, 12 mm lens</td><td class="num">2.530 px/mm</td><td class="num">202 px</td><td class="num t-ok">0.52&deg;</td><td class="num">0.51 m</td></tr>
  </tbody>
</table>
</div>
<p><b>So the instinct is right.</b> A readout-mode change and a six-millimetre lens &mdash
no new sensor, no new geometry &mdash; take face angle from 4.4&deg; to about <b>one degree</b>, which is comparator territory. The optical route is not dead; it has been starved.</p>

<div class="panel flag">
  <h3>Except that resolution is not free, and this is the part that bites</h3>
  <p>Motion blur is measured in millimetres of travel, and converting it to pixels uses the same plate scale. <b>Doubling the resolution doubles the blur.</b> At a club speed of 30 m/s:</p>
</div>
<div class="scroll">
<table>
  <thead><tr><th>Configuration</th><th class="num">blur at today&rsquo;s 300 &micro;s</th><th class="num">exposure for 1 px of smear</th><th class="num">light required</th></tr></thead>
  <tbody>
    <tr><td>today</td><td class="num">2.7 px</td><td class="num">113 &micro;s</td><td class="num">2.7&times;</td></tr>
    <tr><td>1:1 readout, same lens</td><td class="num">5.3 px</td><td class="num">56 &micro;s</td><td class="num">5.3&times;</td></tr>
    <tr><td><b>1:1 readout, 6 mm lens</b></td><td class="num t-bad">11.4 px</td><td class="num">26 &micro;s</td><td class="num t-bad">11.4&times;</td></tr>
    <tr><td>1:1 readout, 12 mm lens</td><td class="num t-bad">22.8 px</td><td class="num">13 &micro;s</td><td class="num t-bad">22.8&times;</td></tr>
  </tbody>
</table>
</div>
<p>The blur model checks out against the session: it predicts 2.7 px today, and 2.97 px was measured. So the configuration that reaches a one-degree face angle also needs an exposure eleven times shorter, which at equal signal-to-noise needs <b>eleven times the light</b> &mdash
and this system is committed to ambient light with no strobe.</p>
<div class="panel warn">
  <h3>The constraint was never pixels. It is the light budget.</h3>
  <p>Pixels are cheap: a readout mode and a lens. What they cost is exposure, and exposure is the one thing the no-strobe rule fixes. <b>Every gain in resolution is a demand for light, one for one</b>, and that is the wall the last several sections have actually been hitting.</p>
  <p>It also reframes the shot that started all this. The 22-shot session was captured at 250&ndash
  300 &micro;s because that is what the bay would give; the first session was clipped at 500 &micro;s. The exposure ladder was being climbed for brightness. It now needs to be climbed for <em>blur</em>, which pulls the other way, and the amount of headroom between them is the real specification for this camera.</p>
</div>

<h3>What that means for the two routes</h3>
<div class="scroll">
<table>
  <thead><tr><th>Route to face angle</th><th class="num">reaches</th><th>what it costs</th></tr></thead>
  <tbody>
    <tr><td>ball direction + club path</td><td class="num t-ok">0.9&ndash;2.3&deg;</td><td>no new hardware &mdash; but needs the club-path channel, currently rejected on 21 of 21 shots, and an absolute yaw calibration</td></tr>
    <tr><td>silhouette, 1:1 readout + 6 mm lens</td><td class="num t-ok">1.0&deg;</td><td>a lens and a readout mode &mdash; and <b>11&times; the light</b>, plus a field of view down to 1.01 m and a frame rate down to about 144 fps</td></tr>
  </tbody>
</table>
</div>
<p>They are complementary rather than competing, and the honest comparison favours doing the cheap one first. The ball route needs no optics at all
what stands between it and a working face angle is a club-path channel that fails on every shot. The optical route needs no club path, but it needs a light budget nobody has measured yet.</p>
<p>One secondary consequence is worth noting because it helps both routes at once: at 1:1 with a 6 mm lens the <b>ball</b> spans 54 px instead of 12.8. The one-sided ball-centre bias that sets every millimetre figure on this page &mdash
1.85 px missing off the top, about 6 mm of range &mdash; largely evaporates at that scale. The ball is not moving nearly as fast as the clubhead before impact, so it does not pay the same blur penalty.</p>
<p><b>The next measurement is therefore not another estimator.</b> It is a light meter in the actual bay: how many lux are available, and what exposure does a usable signal-to-noise ratio need at 1:1? That single number decides whether the optical route is a one-degree instrument or a four-degree one, and no amount of further analysis on the existing footage can answer it.</p>
"""

OUT.write_text(SEC, encoding="utf-8")
print(f"wrote {OUT}")
