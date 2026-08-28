"""Add the 2026-08-26/27 findings to the page, and fix what they superseded.

Two claims on the page are now wrong, and both are wrong in the same direction
-- they treat the ball's range as the club's range:

  * Section 11f's table scores the free-depth fits at 1180 mm and 1336 mm as
    "error against the tape -401 mm / -245 mm", and presents pinning to 1581 mm
    as the fix. The radar has since put the clubhead at 1.042-1.571 m across
    the very frames being fitted, so 1180-1336 mm is INSIDE the club's physical
    range. The fit was tracking the club; pinning it moved it onto the ball.
  * Section 11's IoU 0.636 is quoted as a representative fit quality. It cannot
    be reproduced by any code now in the repository, and IoU has since been
    measured as noise-limited rather than pose-limited.

Then three new sections for what was measured this week.
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

SECTIONS = """
<h2 id="s13"><span class="secnum">13</span>The wall is the mask, not the metric</h2>
<p class="lede">Two objectives with different failure modes, run over identical masks, gave the same answer &mdash; and the answer was that neither of them was the problem.</p>

<p>Every fit quality number on this page came from silhouette overlap, and &sect;11f measured that overlap running <em>inversely</em> to pose correctness. The obvious response is to change the metric. So we did: <b>chamfer distance</b>, which measures the distance between mask <em>boundaries</em> rather than the area they share, and therefore fails in a completely different way. IoU on a small blob is dominated by area; chamfer is dominated by shape.</p>

<p>It made no difference. Measured across the same 21 shots:</p>
<div class="scroll"><table>
  <tr><th>axis</th><th class="num">IoU resolves to</th><th class="num">chamfer resolves to</th></tr>
  <tr><td>yaw (face angle)</td><td class="num">&plusmn;7&deg;</td><td class="num">&plusmn;10&deg;</td></tr>
  <tr><td>pitch (dynamic loft)</td><td class="num">&plusmn;8.5&deg;</td><td class="num">&plusmn;14&deg;</td></tr>
  <tr><td>roll (lie)</td><td class="num">&plusmn;7&deg;</td><td class="num">&plusmn;9.5&deg;</td></tr>
</table></div>

<p>Refitting from identical seeds under each metric, the two land on poses that differ by a <b>median of 12.7&deg;</b> (n=18, range 5.8&ndash;28.0&deg;). <b>When the choice of objective function moves the answer by more than ten degrees, the data is not deciding the pose &mdash; the metric is.</b></p>

<div class="panel flag">
  <h3>What the limit actually is</h3>
  <p>Perturbing the observed mask by <b>one pixel</b> &mdash; a dilate or erode, which is what a threshold shift does &mdash; moves the score by <b>0.40&ndash;1.45&times;</b> as much as a <b>&plusmn;30&deg; pose error</b>. On four of six shots, chamfer moved <em>more</em> for one pixel of mask than across the entire 60&deg; sweep.</p>
  <p>Measured on the mesh itself, the reason is plain. Projected silhouette width against yaw: <b>0&deg; &rarr; 32 px, 5&deg; &rarr; 32 px, 10&deg; &rarr; 30 px, 20&deg; &rarr; 28 px.</b> <b>The first five degrees of face angle change the silhouette by exactly zero pixels.</b> Ten degrees buys two. A threshold shift moves each boundary about one pixel, so it buys two as well.</p>
  <p><b>One pixel of segmentation error is worth about ten degrees of face angle.</b></p>
</div>

<p>That also explains the anti-correlation rather than merely restating it. Mask area moves <b>3&nbsp;%</b> for 10&deg; of yaw but roughly <b>25&nbsp;%</b> for a one-pixel dilate &mdash; so IoU, whose currency is area, is about <b>eight times more responsive to segmentation quality than to twenty degrees of pose</b>. It was never measuring where the club pointed. It was measuring how clean the mask was, and the arms with cleaner masks scored better while pointing the wrong way.</p>

<p><b>The consequence for anyone picking this up: do not spend time on the objective function.</b> Two metrics with independent failure modes reaching the same limit is a stronger result than either alone. The fit is <b>noise-limited, not objective-limited</b>, and the levers that matter are sub-pixel edges and plate scale.</p>

<h2 id="s14"><span class="secnum">14</span>The range was pinned to the wrong object</h2>
<p class="lede">Every mesh fit on this page renders the clubhead at the range of the ball. The club is not there, and the radar has been measuring where it actually is the whole time.</p>

<p>The fitter places the mesh at a fixed <b>1581&nbsp;mm</b>, the tape-measured camera-to-ball distance. But the clubhead arrives from behind the ball and sweeps <em>through</em> that value during exactly the frames being fitted. Running the production club tracker over the raw radar cube for shot 002 puts it at <b>1.042&nbsp;m</b> five frames before impact and <b>1.571&nbsp;m</b> at contact &mdash; a <b>529&nbsp;mm</b> sweep &mdash; extrapolating to the tee with 29&nbsp;mm of error.</p>

<p>Across all 21 shots the radar's own summary fields say the same thing, and confirm the tape independently:</p>
<pre><code>track start        1238 +- 24 mm
+ range_rate x span
= track end        1632 +- 53 mm      tee ball, by tape: 1581 mm</code></pre>
<p>Two sensors that share no hardware, agreeing on where the ball is to within one standard deviation. That is worth having on its own.</p>

<div class="panel ok">
  <h3>An orientation-free test</h3>
  <p>If the club recedes, its silhouette must shrink; rendered area goes as 1/r&sup2;. Observed clubhead mask area, last pre-impact frame divided by first, n=10 shots:</p>
  <p><b>observed 0.829 &plusmn; 0.222</b> &nbsp;&middot;&nbsp; radar ramp predicts <b>0.813</b> &nbsp;&middot;&nbsp; constant range predicts <b>1.000</b></p>
  <p>Constant range is <b>rejected at p &asymp; 0.04</b>; the ramp is consistent at p &asymp; 0.8. This test touches no orientation parameter at all, so it cannot be rescued by refitting angles.</p>
</div>

<p>This is a mechanism for the anti-correlation in &sect;13, not just a second symptom. With the model systematically under-scaled on the early frames, <b>whichever pose renders biggest matches best &mdash; regardless of where it points.</b> The orientation angles were the only free parameters left to absorb a scale error, and that is what they were doing.</p>

<div class="panel warn">
  <h3>Re-rendering is not the fix, and one trap is worth naming</h3>
  <p>Simply re-rendering at the correct ramp is <b>neutral</b> (IoU &minus;0.0035, chamfer +0.021&nbsp;px). Orientation was fitted to absorb the constant-range error, so it has to be <em>refitted</em>. That refit is the open task.</p>
  <p>Anchoring the ramp with the ball-track impact estimate (~frame 71.8) instead of the documented trigger-minus-6.0-frames (68.0) mis-anchors it by <b>~270&nbsp;mm</b>, and makes the ramp look actively harmful: IoU &minus;0.018, chamfer +0.673&nbsp;px, <b>0 of 6 shots improved</b>. Same experiment, opposite conclusion, from a 3.8-frame timing error. <b>Do not derive impact time from the ball track</b> &mdash; it extrapolates five frames beyond its data.</p>
</div>

<h2 id="s15"><span class="secnum">15</span>Can the radar see the club&rsquo;s shape?</h2>
<p class="lede">Not directly &mdash; it is a third of one pixel across the clubhead. But a rotating target spreads its own Doppler, and that spread is measurable.</p>

<p>The tempting idea is that a radar pinging thousands of times a second must be building up a picture. It is not. Angular resolution comes from <em>aperture</em>, and the IWR's is 19.3&nbsp;mm wide:</p>
<div class="scroll"><table>
  <tr><th>quantity</th><th class="num">value</th></tr>
  <tr><td>wavelength (62 GHz)</td><td class="num">4.835 mm</td></tr>
  <tr><td>range resolution</td><td class="num">46.9 mm</td></tr>
  <tr><td>aperture (8 virtual elements at &lambda;/2)</td><td class="num">19.3 mm</td></tr>
  <tr><td>beamwidth</td><td class="num">12.7&deg;</td></tr>
  <tr><td><b>cross-range cell at 1.25 m</b></td><td class="num t-bad"><b>277 mm</b></td></tr>
  <tr><td>clubhead, for comparison</td><td class="num">90 mm &mdash; 0.33 cells</td></tr>
</table></div>
<p>Clubhead, shaft and hands all fall in one angular cell. More chirps buy signal-to-noise and velocity resolution; they never buy spatial resolution.</p>

<p><b>But the club rotates</b>, and a rotating target synthesises its own aperture &mdash; this is ISAR. At <b>1289&deg;/s</b> the head turns <b>15.1&deg;</b> across the 11.7&nbsp;ms the radar tracks it, giving <code>&lambda;/(2&Delta;&theta;)</code> = <b>9.2&nbsp;mm</b> of cross-range resolution. A <b>30&times;</b> improvement, about ten cells across the head. Micro-Doppler gives an identical 9.8 cells, as it must &mdash; after bulk motion is removed, ISAR cross-range <em>is</em> Doppler.</p>

<p>So we opened the raw radar cube for the first time, corrected range walk (the club crosses 1.2 range bins inside a single coherent window), and measured the Doppler width where the production tracker puts the clubhead. <b>21 shots, 112 frames:</b></p>
<div class="scroll"><table>
  <tr><th>measurement</th><th class="num">native Doppler bins</th></tr>
  <tr><td>point-target floor, same estimator and window</td><td class="num">1.27</td></tr>
  <tr><td>predicted from rotation alone (838 Hz)</td><td class="num">1.36</td></tr>
  <tr><td>expected in quadrature</td><td class="num">1.86</td></tr>
  <tr><td><b>measured clubhead median</b></td><td class="num t-ok"><b>1.95</b></td></tr>
</table></div>
<p>That implies <b>893 Hz</b> of toe-to-heel spread against <b>838 Hz</b> predicted &mdash; seven percent high. <b>The clubhead's return is genuinely broader than a point target, by close to the amount its rotation should produce.</b></p>

<div class="panel flag">
  <h3>This is not yet evidence of rotation, and the reason is instructive</h3>
  <p>The discriminating test is whether the width scales with club speed &mdash; rotation does, a merely extended target does not. It came back <b>negative</b> (r = &minus;0.33). Before reading that as a refutation we checked whether the test could work at all, and <b>it cannot</b>:</p>
  <p>&bull; The predicted effect across the full speed range is <b>0.154 bins</b> against an observed scatter of <b>0.490 bins</b> &mdash; the effect is <b>3.2&times; smaller than the noise</b>.<br>
     &bull; Club type is <b>perfectly confounded</b> with speed: 7-iron 37.4&ndash;38.8 m/s, 9-iron 34.6&ndash;36.4 m/s, <b>zero overlap</b>.<br>
     &bull; <b>23&nbsp;% of frames come out narrower than a point target</b>, which is physically impossible and marks a noisy estimator at 12 samples.</p>
  <p>So the magnitude matches and the causation is unproven. Two things would settle it: a capture spanning a <b>wide</b> club-speed range (a driver and a wedge, not a 7-iron and a 9-iron), and a positive control on the <b>ball</b>, which spins fast enough to predict ~8 bins of spread &mdash; if the same estimator shows that, the chain works; if the ball reads point-like, the method is broken. <b>Neither has been run.</b></p>
</div>

<p>If the rotation is real, the prize is not the picture. <b>ISAR cannot be focused without estimating the target's rotation rate and axis</b> &mdash; and those are precisely the two parameters the mesh fit currently leaves free and watches absorb noise. It may also reframe the club-path rejection: the azimuth phase span that rejects all 21 shots runs <b>3&ndash;10&times; larger than a point target can produce</b>, and scatterer migration across a rotating extended body is exactly what ISAR exploits. The signal being discarded as noise may be the rotation itself. <b>Untested.</b></p>
"""

CLOSING = """
<h2 id="s16"><span class="secnum">16</span>Ideas not yet tried</h2>
<p class="lede">Kept separate from the roadmap at the top because none of these has a measurement behind it yet. They are candidates, not commitments.</p>
<div class="scroll"><table>
  <tr><th>Idea</th><th>Why it might work</th><th>What would kill it</th></tr>
  <tr><td><b>Sub-pixel edge extraction</b></td><td>The masks come from a hard threshold on a background difference. Given that one pixel is worth ~10&deg; of face angle, a sub-pixel boundary is worth more than any change to the fit itself.</td><td>Motion blur may already smear the edge past sub-pixel meaning &mdash; the club moves ~3 px during exposure.</td></tr>
  <tr><td><b>Use the OPS speed as a phase-unwrapping prior</b></td><td>OPS club speed is already used to <em>select</em> the radar track (<code>track_selection_mode = ops_speed_prior</code>) but appears not to be used to unwrap phase.</td><td>The rejection is on azimuth phase, not radial &mdash; the OPS prior may simply not constrain the right quantity.</td></tr>
  <tr><td><b>Fit the whole sequence as one rigid body</b></td><td>Already partly done, and it is the only formulation that spends the radar's speed measurement. Combined with a radar-pinned rotation axis it would drop 2 of 5 free parameters.</td><td>If segmentation noise dominates, fewer parameters will not help &mdash; it will just move the noise somewhere else.</td></tr>
  <tr><td><b>A second camera</b></td><td>Stereo resolves depth directly and would end the range argument entirely.</td><td>Cost, synchronisation, and it does nothing for the segmentation limit, which is the binding one.</td></tr>
  <tr><td><b>Mark the club for the measuring rig only</b></td><td>The shipped product must be markerless, but a <em>calibration</em> rig need not be. Foot spray or a dot on the face would give per-frame truth to score the markerless estimator against.</td><td>Nothing, technically &mdash; it is purely a question of whether anyone builds it.</td></tr>
</table></div>
"""


def main() -> None:
    page = PAGE.read_text(encoding="utf-8")
    edits = 0

    def sub(old: str, new: str) -> None:
        nonlocal page, edits
        assert old in page, f"NOT FOUND: {old[:70]!r}"
        assert page.count(old) == 1, f"AMBIGUOUS: {old[:70]!r}"
        page = page.replace(old, new, 1)
        edits += 1

    if '<h2 id="s13">' in page:
        raise SystemExit("new sections already present -- edit them in place instead")

    # 1. Section 11f scored the free-depth fits as ERRORS against the ball range.
    sub(
        "error against the tape",
        "error against the BALL range &mdash; see &sect;14, this framing is wrong",
    )

    # 2. Section 11's representative IoU cannot be reproduced.
    sub(
        "shot 017, a 9-iron whose median IoU of 0.636 sits almost exactly at the median "
        "for the session, so it is a representative shot rather than a flattering one.",
        "shot 017, a 9-iron whose median IoU of 0.636 sits almost exactly at the median "
        "for the session, so it is a representative shot rather than a flattering one. "
        "<b>Later correction:</b> that 0.636 <b>cannot be reproduced by any code now in "
        "the repository</b> &mdash; the committed tracker returns 0.292 on this shot and a "
        "careful rebuild returns 0.452. Treat the figure as unverifiable. &sect;13 and "
        "&sect;14 explain why chasing it was the wrong instinct anyway.",
    )

    # 3. The new sections go after 11j, before "Contribute a capture".
    anchor = '<h2><span class="secnum">12</span>'
    assert anchor in page and page.count(anchor) == 1, (
        "section 12 anchor missing/ambiguous"
    )
    page = page.replace(
        anchor, SECTIONS.strip() + "\n\n" + CLOSING.strip() + "\n\n" + anchor, 1
    )
    edits += 1

    PAGE.write_text(page, encoding="utf-8")
    print(f"{edits} edits applied to {PAGE}")


if __name__ == "__main__":
    main()
