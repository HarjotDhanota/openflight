"""Correct the impact-timing section of the technical report.

The report stated a 6.0-frame acoustic trigger lag as measured fact and warned
readers off the ball-track estimate. Both are wrong, and in the same direction:
impact is 2.11 frames after... before the trigger, not 6.0, and the ball track
was right all along.

The correction is placed prominently rather than quietly patched, because the
wrong figure anchored every range and pose result in the report.
"""

from __future__ import annotations

from pathlib import Path

SRC = Path("research/silhouette_poc/falsification/build_report.py")
s = SRC.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global s
    assert old in s, f"NOT FOUND: {old[:70]!r}"
    assert s.count(old) == 1, f"AMBIGUOUS ({s.count(old)}): {old[:70]!r}"
    s = s.replace(old, new, 1)


# 1. Replace the impact-timing subsection wholesale.
sub(
    """<h3>Impact timing</h3>
<p>The acoustic trigger fires <b>6.0 &plusmn; 0.68 frames</b> (12.8 &plusmn; 1.5&nbsp;ms) after contact. Camera and radar locate impact independently and agree to <b>0.66 frames</b>.</p>
<div class="panel warn">
  <h3>Do not derive impact time from the ball's image track</h3>
  <p>A quadratic fit to the departing ball extrapolates roughly five frames beyond its own data and returns an impact frame about 3.8 frames late. On one shot it places launch <em>after</em> the trigger fired, which is impossible. Rendering the frames directly settles it: the clubhead reaches the ball at frame 67, covers the tee through frame 72, and the ball is clear by frame 73.</p>
  <p>This matters beyond timing. Anchoring the range model in &sect;{_num("pose-range")} with the extrapolated value instead of the measured one shifts it by roughly 270&nbsp;mm and reverses the conclusion.</p>
</div>""",
    """<h3>Impact timing</h3>
<p>Contact precedes the acoustic trigger by the time the sound takes to reach the unit:</p>
<pre><code>impact_time = trigger_time  -  distance_to_microphone / speed_of_sound(T)</code></pre>
<p>On this rig the ball sits <b>1.575&nbsp;m</b> from the unit, giving <b>4.59&nbsp;ms</b> or <b>2.11 frames</b> at 468&nbsp;fps. Two independent routes agree:</p>
<div class="scroll"><table>
  <tr><th>Method</th><th class="num">Impact frame</th></tr>
  <tr><td>Acoustic model &mdash; distance &divide; speed of sound</td><td class="num">71.85</td></tr>
  <tr><td>Ball departure, measured per shot (n=20)</td><td class="num">71.89 &plusmn; 0.77</td></tr>
</table></div>
<p>They agree to <b>0.04 frames</b>. The SEN-14262 hardware path is about 10&nbsp;&micro;s and is negligible; there is no unexplained detector latency.</p>
<div class="panel flag">
  <h3>This corrects a figure published earlier in this report</h3>
  <p>Earlier versions stated a <b>6.0-frame</b> lag and warned readers off the ball-track estimate. <b>Both were wrong.</b> The 6.0 constant put contact at frame 68 on every shot &mdash; off by <b>3.89 frames</b>, about 8&nbsp;ms &mdash; and the ball track, which was dismissed, was correct.</p>
  <p>The error came from misreading a render. The clubhead is <b>27&nbsp;px wide</b> and sits adjacent to the ball for two or three frames before it strikes, so "the head reaches the ball" is not contact. Contact is when the <b>ball starts moving</b>. Per-shot values run <b>70.65 to 73.64</b>, not a constant.</p>
  <p><b>Everything anchored on that constant is superseded</b> &mdash; the clubhead range model in &sect;{_num("pose-range")}, the pose fits, and a claimed "post-impact frames were fitted" defect that was an artefact of the wrong anchor.</p>
</div>
<div class="panel warn">
  <h3>The lag belongs to the installation, not to the software</h3>
  <p>It scales with how far the unit sits from the ball, so a fixed frame offset is only ever right for the rig it was measured on. At 468&nbsp;fps, with the trigger at frame 74:</p>
  <div class="scroll"><table>
    <tr><th>Ball to unit</th><th class="num">Lag</th><th class="num">Impact frame</th></tr>
    <tr><td>1.0 m</td><td class="num">2.91 ms</td><td class="num">72.64</td></tr>
    <tr><td><b>1.575 m &mdash; this rig</b></td><td class="num">4.59 ms</td><td class="num">71.85</td></tr>
    <tr><td>2.5 m</td><td class="num">7.28 ms</td><td class="num">70.59</td></tr>
    <tr><td>3.5 m</td><td class="num">10.20 ms</td><td class="num">69.23</td></tr>
  </table></div>
  <p>Distance dominates: doubling it doubles the lag, while the whole 0&ndash;40&nbsp;&deg;C range moves the speed of sound about 7&nbsp;%. Shipped as <code>src/openflight/acoustic.py</code> with 21 tests. <code>tee_range_m</code> in <code>iwr6843/calibration.py</code> is the distance source.</p>
</div>
<div class="panel ok">
  <h3>Production already solved this; the research code did not use it</h3>
  <p><code>iwr6843/shot.py:impact_time_s</code> back-extrapolates the <em>ball's own range walk</em> to the tee rather than trusting the trigger, and its docstring records why: assuming the trigger's ring position "is why the club-path estimator was fitting the follow-through". <code>camera/club_delivery.py</code> likewise <em>detects</em> the impact frame and uses the trigger only as a &plusmn;8/+10 frame plausibility gate.</p>
  <p><b>So the production measurement paths are not affected.</b> The defect was confined to the research scripts, which reinvented a solved problem and got it wrong. An earlier draft of this panel claimed a production-wide 4.6&nbsp;ms bias; that claim was itself incorrect and is withdrawn.</p>
</div>""",
)

# 2. Flag it in the summary, where a reader who stops early will see it.
sub(
    "<p>The highest-value next step is not another estimator.",
    '<div class="panel flag">\n'
    "  <h3>Correction affecting several figures in this report</h3>\n"
    "  <p>The acoustic trigger lag was previously stated as <b>6.0 frames</b>. It is "
    "<b>2.11 frames</b> on this rig &mdash; the sound's travel time over 1.575&nbsp;m &mdash; "
    "and it varies with how far the unit sits from the ball. The wrong constant put contact "
    "at frame 68 on every shot instead of a per-shot value near 71.9, and it anchored the "
    "clubhead range model and every pose fit below. Those results are <b>superseded</b>; "
    "see &sect;03. Production measurement paths are unaffected.</p>\n"
    "</div>\n\n"
    "<p>The highest-value next step is not another estimator.",
)

# 3. Reproducibility table gains the new module.
sub(
    "  <tr><td>Scoring primitives and their unit tests</td>",
    "  <tr><td>Acoustic trigger timing</td><td><code>src/openflight/acoustic.py</code>, "
    "<code>tests/test_acoustic.py</code></td></tr>\n"
    "  <tr><td>Delivered loft / face angle / lie</td>"
    "<td><code>replay/club_angles.py</code>, <code>tests/test_club_angles.py</code></td></tr>\n"
    "  <tr><td>Scoring primitives and their unit tests</td>",
)

# 4. The appendix gets the retraction, alongside the others.
sub(
    "  <tr><td>The trigger lags impact by 2.11 frames; contact is at frame 71.9</td>"
    "<td><b>Retracted.</b> The original 6.0-frame figure is correct. Derived from a "
    "ball-track extrapolation running five frames beyond its data.</td></tr>",
    "  <tr><td>The trigger lags impact by 2.11 frames &mdash; then retracted in favour of "
    "6.0 frames</td><td><b>The retraction was the error.</b> 2.11 frames is correct and "
    "equals the acoustic time of flight over 1.575&nbsp;m. Confirmed by the ball track "
    "(71.89 &plusmn; 0.77, n=20) and by the physics, agreeing to 0.04 frames. The 6.0 "
    "figure came from misreading a render.</td></tr>\n"
    "  <tr><td>Production carries a ~4.6&nbsp;ms impact-timing bias</td><td><b>Withdrawn.</b> "
    "Both production measurement paths derive impact from the data, not the trigger.</td></tr>",
)

SRC.write_text(s, encoding="utf-8")
print("build_report.py corrected")
