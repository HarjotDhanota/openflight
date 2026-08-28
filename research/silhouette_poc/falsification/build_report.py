"""Build the technical report for the OpenFlight team.

This is a separate document from `openflight-impact-location.html`, which stays
as it is. That page is a working log: chronological, corrected in place, and
useful for tracing how a conclusion was reached. This one is the report -- the
same evidence, reorganised by subject, with the narrative of discovery removed
and the retracted material moved to an appendix.

Figures are lifted verbatim from the log so the two documents cannot disagree
about what an image shows. Eleven of the log's sixteen are carried over; the
rest were duplicates of a better figure elsewhere. The interactive frame player
is deliberately left behind: it accounts for 5.4 MB of the log's 10 MB and adds
nothing a static figure does not.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

OUT_DIR = Path(
    os.environ.get("OPENFLIGHT_PAGE_OUT", Path(__file__).resolve().parent / "page")
)
SOURCE = OUT_DIR / "openflight-impact-location.html"
TARGET = OUT_DIR / "openflight-clubface-report.html"

# Figure indices in the source, in the order they appear there.
FIGURES = {
    "exposure": 1,  # ball at address vs airborne
    "mesh_faces": 2,  # mesh rendered front and back
    "raw_frames": 5,  # F58-F80 raw sensor pixels
    "fit_overlay": 6,  # shot 014 observed vs projected
    "range_time": 7,  # radar range-time map
    "ball_flat": 11,  # teed ball flattening
    "pose_3d": 12,  # three-view pose reconstruction
    "cavity": 13,  # cavity back, face-on
    "striking": 14,  # the actual striking face
    "three_arms": 15,  # one shot fitted three ways
    "scale_gap": 16,  # model vs observed at the tape range
}

CSS = """
:root{--bg:#f7f6f2;--surface:#fff;--ink:#22261f;--ink-soft:#5c6156;--line:#d8d6cc;
  --cam:#0e7c7b;--radar:#b3690a;--good:#2e7d43;--bad:#b3402e;--warn:#8a6d1f;--code:#eeede7}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#191c17;--surface:#21251f;
  --ink:#e8e7df;--ink-soft:#a4a89b;--line:#3a3f36;--cam:#4cc4c0;--radar:#e0a04a;--good:#6fc184;
  --bad:#e07b66;--warn:#d6b45e;--code:#262a23}}
:root[data-theme="dark"]{--bg:#191c17;--surface:#21251f;--ink:#e8e7df;--ink-soft:#a4a89b;
  --line:#3a3f36;--cam:#4cc4c0;--radar:#e0a04a;--good:#6fc184;--bad:#e07b66;--warn:#d6b45e;
  --code:#262a23}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"IBM Plex Sans",system-ui,sans-serif;font-size:16px;line-height:1.62}
.wrap{max-width:960px;margin:0 auto;padding:44px 22px 110px}
h1{font-family:"IBM Plex Sans Condensed",sans-serif;font-weight:700;
  font-size:clamp(28px,4.6vw,42px);line-height:1.08;margin:0 0 12px;text-wrap:balance}
h2{font-family:"IBM Plex Sans Condensed",sans-serif;font-weight:700;font-size:24px;
  margin:60px 0 6px;text-wrap:balance;scroll-margin-top:22px}
h3{font-family:"IBM Plex Sans Condensed",sans-serif;font-weight:600;font-size:18.5px;
  margin:34px 0 2px;text-wrap:balance;scroll-margin-top:22px}
.secnum{font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--ink-soft);
  letter-spacing:.09em;display:block;margin-bottom:6px}
p{margin:12px 0;max-width:72ch}
.lede{color:var(--ink-soft);max-width:70ch;margin:0 0 10px;font-size:17.5px}
.sub{color:var(--ink-soft);max-width:72ch;margin:6px 0 0;font-size:15px}
a{color:var(--cam)}
code{font-family:"IBM Plex Mono",monospace;font-size:.87em;background:var(--code);
  padding:1px 6px;border-radius:4px}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:14px 16px;
  overflow-x:auto;font-family:"IBM Plex Mono",monospace;font-size:13px;line-height:1.6}
pre code{background:none;padding:0}
figure{margin:26px 0}
figure img{width:100%;height:auto;display:block;border:1px solid var(--line);
  border-radius:8px;background:#101210;image-rendering:pixelated}
figcaption{font-size:13.5px;color:var(--ink-soft);margin-top:9px;max-width:82ch;line-height:1.5}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:18px 22px;margin:24px 0}
.panel.flag{border-left:3px solid var(--bad)}
.panel.ok{border-left:3px solid var(--good)}
.panel.warn{border-left:3px solid var(--warn)}
.panel h3{margin:0 0 8px;font-size:16px;font-family:"IBM Plex Sans",sans-serif;font-weight:600}
.panel p{font-size:14.6px;color:var(--ink-soft);margin:7px 0 0;max-width:78ch}
.panel p:first-of-type{margin-top:0}
.scroll{overflow-x:auto;margin:14px 0}
table{border-collapse:collapse;width:100%;font-size:14.6px}
th{text-align:left;font-family:"IBM Plex Mono",monospace;font-weight:500;font-size:11.5px;
  letter-spacing:.06em;text-transform:uppercase;color:var(--ink-soft);
  padding:8px 16px 8px 0;border-bottom:1px solid var(--line);white-space:nowrap}
th.num{text-align:right}
td{padding:9px 16px 9px 0;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
td.num{text-align:right;font-variant-numeric:tabular-nums;font-family:"IBM Plex Mono",monospace;
  font-size:13.5px;white-space:nowrap}
.t-bad{color:var(--bad);font-weight:600}
.t-ok{color:var(--good);font-weight:600}
.t-warn{color:var(--warn);font-weight:600}
.meta{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--ink-soft);
  letter-spacing:.04em;margin:0 0 26px;text-transform:uppercase}
/* ---- section rail ---- */
#rail{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:14px 18px;margin:0 0 32px;font-size:14px}
#rail summary{font-family:"IBM Plex Sans Condensed",sans-serif;font-weight:700;font-size:16px;
  cursor:pointer;list-style:none}
#rail summary::-webkit-details-marker{display:none}
#rail summary::after{content:"  tap to collapse";font-family:"IBM Plex Mono",monospace;
  font-size:11px;color:var(--ink-soft);font-weight:400;letter-spacing:.04em;white-space:pre}
#rail[open] summary{margin-bottom:10px}
.rail-body{max-height:min(44vh,320px);overflow-y:auto}
#rail ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:1px}
#rail a{display:block;padding:4px 8px 4px 10px;color:var(--ink-soft);text-decoration:none;
  border-left:2px solid transparent;border-radius:0 4px 4px 0;line-height:1.35;font-size:13.4px}
#rail a.sub-link{padding-left:30px;font-size:12.8px}
#rail a:hover{color:var(--ink);background:var(--code)}
#rail a:focus-visible{outline:2px solid var(--cam);outline-offset:1px}
#rail a.here{color:var(--ink);border-left-color:var(--cam);background:var(--code);font-weight:600}
.rail-n{font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--ink-soft);
  margin-right:7px;letter-spacing:.04em}
@media (min-width:1240px){
  body{padding-left:250px}
  #rail{position:fixed;top:0;left:0;bottom:0;width:250px;margin:0;border:0;border-radius:0;
    border-right:1px solid var(--line);padding:30px 12px 48px 22px;overflow-y:auto;z-index:5}
  #rail summary{display:none}
  .rail-body{max-height:none;overflow:visible}
}
@media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
"""

JS = """
(function(){
  var links = Array.prototype.slice.call(document.querySelectorAll('#rail a[href^="#"]'));
  if(!links.length) return;
  var byId = {};
  links.forEach(function(a){ byId[a.getAttribute('href').slice(1)] = a; });
  var targets = Object.keys(byId).map(function(id){ return document.getElementById(id); })
    .filter(Boolean);
  if(!targets.length) return;
  var current = null;
  function mark(id){
    if(id === current) return;
    if(current && byId[current]) byId[current].classList.remove('here');
    current = id;
    var a = byId[id];
    if(!a) return;
    a.classList.add('here');
    var rail = document.getElementById('rail');
    if(rail && window.matchMedia('(min-width:1240px)').matches){
      var top = a.offsetTop, bot = top + a.offsetHeight;
      if(top < rail.scrollTop || bot > rail.scrollTop + rail.clientHeight){
        rail.scrollTop = top - rail.clientHeight / 2;
      }
    }
  }
  function update(){
    var line = 90, best = targets[0];
    for(var i = 0; i < targets.length; i++){
      if(targets[i].getBoundingClientRect().top <= line) best = targets[i];
    }
    mark(best.id);
  }
  var ticking = false;
  window.addEventListener('scroll', function(){
    if(ticking) return;
    ticking = true;
    window.requestAnimationFrame(function(){ update(); ticking = false; });
  }, {passive:true});
  window.addEventListener('resize', update, {passive:true});
  update();
})();
"""

# (id, title, level) -- level 2 entries are indented in the rail.
OUTLINE = [
    ("summary", "Summary", 2),
    ("setup", "Measurement setup", 2),
    ("validated", "Validated measurements", 2),
    ("pose", "Clubhead pose estimation", 2),
    ("pose-model", "The club model and its reference frame", 3),
    ("pose-fit", "Fitting the model to real frames", 3),
    ("pose-limits", "Information available in the silhouette", 3),
    ("pose-seg", "Segmentation as the binding constraint", 3),
    ("pose-range", "The clubhead range model", 3),
    ("pose-metric", "Objective function selection", 3),
    ("radar", "Radar contribution", 2),
    ("radar-now", "What the radar measures today", 3),
    ("radar-path", "Club path and attack angle", 3),
    ("radar-isar", "Cross-range resolution and Doppler", 3),
    ("comparators", "Comparison with commercial systems", 2),
    ("recommendations", "Recommendations", 2),
    ("reproduce", "Reproducing this work", 2),
    ("appendix", "Appendix: corrections to earlier claims", 2),
]


def extract_figures(source_html: str) -> dict[str, str]:
    """Pull the chosen <figure> blocks out of the working log, verbatim."""
    blocks = re.findall(r"<figure>.*?</figure>", source_html, re.S)
    assert len(blocks) >= max(FIGURES.values()), (
        f"source has {len(blocks)} figures, need at least {max(FIGURES.values())}"
    )
    out = {}
    for name, index in FIGURES.items():
        block = blocks[index - 1]
        assert "base64," in block, f"figure {name} (#{index}) carries no image data"
        out[name] = block
    return out


def rail(outline) -> str:
    rows = []
    number = 0
    for anchor, title, level in outline:
        if level == 2:
            number += 1
            rows.append(
                f'<li><a href="#{anchor}"><span class="rail-n">{number:02d}</span>{title}</a></li>'
            )
        else:
            rows.append(f'<li><a class="sub-link" href="#{anchor}">{title}</a></li>')
    return (
        '<details id="rail" open>\n<summary>Contents</summary>\n<div class="rail-body">\n<ul>'
        + "".join(rows)
        + "</ul>\n</div>\n</details>"
    )


def heading(anchor: str, outline) -> str:
    """Render the <h2>/<h3> for an outline entry, numbered like the rail."""
    number = 0
    for a, title, level in outline:
        if level == 2:
            number += 1
        if a == anchor:
            if level == 2:
                return f'<h2 id="{anchor}"><span class="secnum">{number:02d}</span>{title}</h2>'
            return f'<h3 id="{anchor}">{title}</h3>'
    raise KeyError(anchor)


def build(figures: dict[str, str]) -> str:
    parts = [
        '<meta charset="utf-8">',
        "<title>Technical Report</title>",
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        "family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600"
        '&family=IBM+Plex+Mono:wght@400;500&display=swap">',
        f"<style>{CSS}</style>",
        '<div class="wrap">',
        rail(OUTLINE),
        "<h1>Markerless clubface impact location</h1>",
        '<p class="meta">OpenFlight &middot; technical report &middot; '
        "session 20260825_181734, 21 shots</p>",
        '<p class="lede">An assessment of whether clubface impact location and face angle '
        "can be measured from a single behind-ball camera under ambient light, with no "
        "markers on the ball or club. This report states what has been measured, what has "
        "not, and what would resolve the remaining questions.</p>",
        _summary(),
        _setup(figures),
        _validated(figures),
        _pose(figures),
        _radar(figures),
        _comparators(),
        _recommendations(),
        _reproduce(),
        _appendix(),
        "</div>",
        f"<script>{JS}</script>",
    ]
    return "\n".join(p for p in parts if p)


def _summary() -> str:
    return f"""
{heading("summary", OUTLINE)}
<p class="lede">Ball flight measurement is working and validated against the radar. Club measurement is not, and the reason is now understood well enough to act on.</p>

<div class="panel ok">
  <h3>Established</h3>
  <p><b>Ball detection</b> succeeds on 21 of 22 captures with no false detections. <b>Impact timing</b> from camera and radar agree to <b>0.66 frames</b>. <b>Camera attitude</b> is measured rather than assumed: boresight pitch <b>&minus;0.185&deg; &plusmn; 0.111&deg;</b>, with the ball centre <b>163&nbsp;mm</b> below the lens. The <b>7-iron/9-iron launch angle difference is real</b> &mdash; an independent reconstruction gives +2.91&deg; against the radar estimator's +2.59&deg;, and it survives a 13&deg; change in assumed camera pitch. Camera and radar independently agree on the tee position to <b>1632 &plusmn; 53&nbsp;mm</b> against a taped <b>1581&nbsp;mm</b>.</p>
</div>

<div class="panel flag">
  <h3>Not established</h3>
  <p><b>Face angle, dynamic loft and impact location</b> are model-dependent inferences, not measurements. <b>No accuracy figure exists for any of them</b>, because the comparison harness does not yet cover club metrics. Radar club path and attack angle are <b>rejected on 21 of 21 shots</b>. Camera and radar disagree by approximately <b>5&deg; in both axes</b>, and no current data arbitrates between them.</p>
</div>

<div class="panel warn">
  <h3>Three findings that determine what to do next</h3>
  <p><b>1. The pose fit is noise-limited, not model-limited.</b> One pixel of segmentation error is worth approximately <b>ten degrees</b> of face angle. The first 5&deg; of face angle change the projected silhouette by <b>zero pixels</b>.</p>
  <p><b>2. The clubhead range is modelled incorrectly.</b> Every fit renders the club at the range of the <em>ball</em>. The radar shows the club traversing <b>529&nbsp;mm</b> of range during the frames being fitted. Constant range is rejected at <b>p &asymp; 0.04</b>.</p>
  <p><b>3. Changing the fit metric does not help.</b> Two objectives with independent failure modes recover poses differing by a median of <b>12.7&deg;</b> &mdash; the same magnitude as the segmentation noise.</p>
</div>

<div class="panel flag">
  <h3>Correction affecting several figures in this report</h3>
  <p>The acoustic trigger lag was previously stated as <b>6.0 frames</b>. It is <b>2.11 frames</b> on this rig &mdash; the sound's travel time over 1.575&nbsp;m &mdash; and it varies with how far the unit sits from the ball. The wrong constant put contact at frame 68 on every shot instead of a per-shot value near 71.9, and it anchored the clubhead range model and every pose fit below. Those results are <b>superseded</b>; see &sect;03. Production measurement paths are unaffected.</p>
</div>

<p>The highest-value next step is not another estimator. The club-metric comparison harness has now been <b>built and shipped</b>; the step is a <b>session alongside a Trackman</b> to give it truth to compare against. Full priorities, including what was completed on 2026-08-27 and what each completion showed, in <a href="#recommendations">Recommendations</a>.</p>
"""


def _setup(f: dict[str, str]) -> str:
    return f"""
{heading("setup", OUTLINE)}
<p>A single monochrome global-shutter camera behind the ball, with two radars. All figures in this report come from one session of 21 correctly exposed shots (7-iron and 9-iron), captured 2026-08-25.</p>

<div class="scroll"><table>
  <tr><th>Parameter</th><th class="num">Value</th><th>Source</th></tr>
  <tr><td>Sensor / mode</td><td class="num">OV9281, 320&times;200</td><td>2&times; subsampled readout</td></tr>
  <tr><td>Frame rate</td><td class="num">467.6 fps</td><td>measured, 0 dropped frames</td></tr>
  <tr><td>Exposure</td><td class="num">247&ndash;298 &micro;s</td><td>measured</td></tr>
  <tr><td>Lens / focal length</td><td class="num">2.8 mm, f<sub>x</sub> = 466.7 px</td><td>datasheet optics, not a calibrated matrix</td></tr>
  <tr><td>Plate scale at the ball</td><td class="num">0.2952 px/mm</td><td>derived; 1 px = 3.39 mm</td></tr>
  <tr><td>Camera height</td><td class="num">203.2 mm</td><td>tape</td></tr>
  <tr><td>Camera-to-ball range</td><td class="num">1581 mm</td><td>tape chain; radar agrees to 1632 &plusmn; 53 mm</td></tr>
  <tr><td>Boresight pitch</td><td class="num">&minus;0.185&deg; &plusmn; 0.111&deg;</td><td>recovered from footage, 21 shots</td></tr>
  <tr><td>Radars</td><td class="num">OPS243-A + IWR6843</td><td>24 GHz Doppler; 62 GHz FMCW</td></tr>
</table></div>

<p><b>Two properties of this configuration constrain everything that follows.</b> The plate scale means the clubhead spans roughly 32 pixels. And there is no distortion model, no independently estimated principal point, and no separate f<sub>x</sub>/f<sub>y</sub> &mdash; the intrinsics are nominal, derived from the datasheet lens over the effective pixel pitch.</p>

<div class="panel">
  <h3>Known gap in the setup record</h3>
  <p>The enclosure used for this session is not dimensioned in any drawing. Camera pitch was recovered from the footage rather than specified, so it is not reproducible on a second unit. Establishing the mounting geometry, and ideally deriving camera attitude from the existing LIS3DH inclinometer as the radar already does, would remove that dependency.</p>
</div>
"""


def _validated(f: dict[str, str]) -> str:
    return f"""
{heading("validated", OUTLINE)}
<p>These measurements are supported by cross-sensor agreement or by an independent reconstruction, and are the parts of the system suitable to build on.</p>

<h3 style="margin-top:26px">Ball detection</h3>
<p>Detection succeeds on 21 of 22 captures with no false positives. The one failure is a capture taken at 495&nbsp;&micro;s and gain 15, which saturated 99.8&nbsp;% of the frame; it is excluded from all analysis in this report.</p>
<p>Detection cannot rely on brightness alone. At address the ball sits against a mat driven past the sensor's ceiling and registers as a <em>dark</em> object; in flight it is the brightest thing in frame. The detector must accommodate both polarities.</p>
{f["exposure"]}

<h3>Ball geometry at the tee</h3>
<p>The teed ball's image is not circular. It is measurably flattened across the top, which biased an earlier radius estimate and, through it, the assumed camera-to-ball range. Fitting the boundary rather than thresholding the bright region removes the bias.</p>
{f["ball_flat"]}

<h3>Impact timing</h3>
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
</div>

<h3>Launch angle</h3>
<p>The 7-iron/9-iron launch angle difference is real and not an artefact of the estimator. An independent reconstruction from camera rays plus radar range walk gives <b>+2.91&deg;</b> and <b>+4.22&deg;</b> against the shipped estimator's +2.59&deg; and +3.60&deg;, and the result is invariant to a 13&deg; change in assumed camera pitch. A separate hypothesis, that the estimator carries a club-dependent bias, was tested and refuted (&minus;0.003&deg;).</p>
"""


def _pose(f: dict[str, str]) -> str:
    return f"""
{heading("pose", OUTLINE)}
<p class="lede">The clubhead is located reliably. Its orientation is not, and the limiting factor is not the fitting method.</p>

{heading("pose-model", OUTLINE)}
<p>The model is a triangle mesh of a Titleist 690CB 7-iron. Pose is solved as a 3D centre plus orientation, projected through the camera model and rasterised; every overlay in this report is the model's own projection, unpadded.</p>
{f["mesh_faces"]}

<div class="panel flag">
  <h3>Defect: the model's reference frame is anchored to the back of the club</h3>
  <p><code>detect_face_plane</code> selects the plane by an extremity criterion. On a cavity-back iron the hosel protrudes past the striking face, so the criterion selects the <b>cavity rim on the reverse side</b>. Measured loft was reported as 17.5&deg;; the true values are <b>33.10&deg; loft and 61.19&deg; lie</b>.</p>
  <p><b>Every dynamic-loft figure derived from the mesh inherits this.</b> The two images below are the two candidate surfaces; the scorelines identify the correct one unambiguously.</p>
</div>
{f["cavity"]}
{f["striking"]}

{heading("pose-fit", OUTLINE)}
<p>Position and scale behave. Orientation does not. The fit locates a clubhead-shaped object in approximately the right place on every pre-impact frame, and the printed angles are the part that should not yet be relied on.</p>
{f["raw_frames"]}
{f["fit_overlay"]}
{f["pose_3d"]}

{heading("pose-limits", OUTLINE)}
<p>The silhouette carries much less orientation information than it appears to. Measured directly on the mesh, projected width against face angle:</p>
<div class="scroll"><table>
  <tr><th>Face angle</th><th class="num">0&deg;</th><th class="num">5&deg;</th><th class="num">10&deg;</th><th class="num">15&deg;</th><th class="num">20&deg;</th><th class="num">30&deg;</th></tr>
  <tr><td>Projected width</td><td class="num">32 px</td><td class="num t-bad">32 px</td><td class="num">30 px</td><td class="num">30 px</td><td class="num">28 px</td><td class="num">26 px</td></tr>
</table></div>
<p><b>The first five degrees of face angle change the silhouette by no pixels at all.</b> Ten degrees changes it by two. This is a property of the projection, not of the fitting method &mdash; a clubhead rotating about the vertical axis presents an almost stationary width, because the thickness rotating into view compensates for the face length rotating out of it.</p>
<p>Sweeping each axis around a fitted pose on real frames and measuring how far it can move before either metric registers a change:</p>
<div class="scroll"><table>
  <tr><th>Axis</th><th class="num">IoU (0.01 threshold)</th><th class="num">Chamfer (0.1 px threshold)</th></tr>
  <tr><td>Yaw &mdash; face angle</td><td class="num">&plusmn;7&deg;</td><td class="num">&plusmn;10&deg;</td></tr>
  <tr><td>Pitch &mdash; dynamic loft</td><td class="num">&plusmn;8.5&deg;</td><td class="num">&plusmn;14&deg;</td></tr>
  <tr><td>Roll &mdash; lie</td><td class="num">&plusmn;7&deg;</td><td class="num">&plusmn;9.5&deg;</td></tr>
</table></div>

{heading("pose-seg", OUTLINE)}
<p>Perturbing the observed mask by <b>one pixel</b> &mdash; a dilation or erosion, which is what a change in segmentation threshold produces &mdash; moves the fit score by <b>0.40 to 1.45 times</b> as much as a <b>&plusmn;30&deg; pose error</b>. On four of six shots, the boundary metric moved further for one pixel of mask than across the entire 60&deg; sweep.</p>
<p><b>One pixel of segmentation error is worth approximately ten degrees of face angle.</b></p>
<p>This also explains why silhouette overlap ran inversely to pose correctness in earlier work. Mask area changes by about <b>3&nbsp;%</b> for 10&deg; of face angle, but by roughly <b>25&nbsp;%</b> for a one-pixel dilation &mdash; so an area-based metric is around eight times more responsive to segmentation quality than to twenty degrees of pose. It was measuring mask quality, and the arms with cleaner masks scored better while recovering worse poses.</p>
<p class="sub">Consequence: the effective levers are sub-pixel edge extraction and plate scale, not the fitting algorithm.</p>

{heading("pose-range", OUTLINE)}
<p>The fitter places the mesh at a fixed 1581&nbsp;mm, the camera-to-ball distance. The clubhead is not at that range during the frames being fitted &mdash; it arrives from behind the ball and passes through it.</p>
<p>Running the production club tracker over the raw radar cube places the clubhead at <b>1.042&nbsp;m</b> five frames before impact and <b>1.571&nbsp;m</b> at contact, a <b>529&nbsp;mm</b> traverse, extrapolating to the tee with 29&nbsp;mm of error. Across all 21 shots the radar's summary fields agree and independently confirm the taped ball position:</p>
<pre><code>track start          1238 +- 24 mm
+ range_rate x span
= track end          1632 +- 53 mm     tee ball, by tape: 1581 mm</code></pre>

<div class="panel ok">
  <h3>Test independent of orientation</h3>
  <p>If the club recedes, its projected area must fall as 1/r&sup2;. Observed clubhead mask area, last pre-impact frame divided by first, n=10 shots:</p>
  <p><b>Observed 0.829 &plusmn; 0.222</b> &middot; radar-derived range model predicts <b>0.813</b> &middot; constant range predicts <b>1.000</b>.</p>
  <p>Constant range is <b>rejected at p &asymp; 0.04</b>; the radar-derived model is consistent at p &asymp; 0.8. This test uses no orientation parameter, so refitting angles cannot account for it.</p>
</div>

<p>This is also a mechanism for the metric behaviour above rather than a separate symptom. With the model systematically under-scaled on the early frames, the pose that projects largest matches best regardless of its orientation &mdash; and the orientation angles were the only free parameters available to absorb a scale error.</p>
{f["scale_gap"]}
<p><b>Correcting the render alone is not sufficient</b> (IoU &minus;0.0035, chamfer +0.021&nbsp;px). Orientation was fitted under the constant-range assumption and must be re-solved with the corrected range model. That work is outstanding.</p>

{heading("pose-metric", OUTLINE)}
<p>Because overlap was suspect, a boundary-distance metric was implemented and evaluated as a replacement. It fails in a different way from overlap &mdash; area versus shape &mdash; so agreement between the two is informative.</p>
<p>Refitting from identical seeds under each metric, the recovered poses differ by a <b>median of 12.7&deg;</b> (n=18, range 5.8&ndash;28.0&deg;).</p>
{f["three_arms"]}
<p><b>When the choice of objective function moves the recovered orientation by more than ten degrees, the data is not determining the pose.</b> Two metrics with independent failure modes reaching the same limit is a stronger result than either alone: the fit is noise-limited, not objective-limited.</p>
<p class="sub">Recommendation: no further effort on the objective function until segmentation quality or plate scale improves.</p>
"""


def _radar(f: dict[str, str]) -> str:
    return f"""
{heading("radar", OUTLINE)}
<p class="lede">The radar already measures quantities the pose fit does not use, and its own club-angle output is rejected on every shot for a reason worth diagnosing.</p>

{heading("radar-now", OUTLINE)}
<p>Impact timing is available to approximately 33&nbsp;&micro;s from the OPS243 30&nbsp;kHz I/Q buffer. The IWR6843 tracks the clubhead's range and range rate through the approach. The range&ndash;time map separates the clubhead, the ball and static clutter cleanly:</p>
{f["range_time"]}
<p>Per shot, the radar currently reports and the pose fit currently discards: clubhead range at track start (<b>1238 &plusmn; 24&nbsp;mm</b>), range rate (approximately <b>33&nbsp;m/s</b>), azimuth rate, and track span. The clubhead travels within about <b>25&deg;</b> of the radar boresight, so 91&nbsp;% of its speed is measured directly.</p>
<p>The 22 raw capture files are included in the session export and are decoded by <code>src/openflight/iwr6843/dump.py</code>, which is production code. Prior to this report the silhouette work had never read them.</p>

{heading("radar-path", OUTLINE)}
<p>Club path and attack angle are rejected on <b>21 of 21 shots</b>, always with status <code>rejected_phase_span</code>. Two observations narrow the cause:</p>
<div class="scroll"><table>
  <tr><th>Observation</th><th>Value</th><th>Expected</th></tr>
  <tr><td>Azimuth phase span</td><td class="num t-bad">2.18&ndash;3.91 rad</td><td class="num">&asymp;1.3 rad; ceiling &pi;/2</td></tr>
  <tr><td>Attack angle, all shots</td><td class="num t-bad">&minus;25.3&deg; to &minus;37.3&deg; (sd 2.8&deg;)</td><td class="num">&asymp;&minus;4&deg; for a 7-iron</td></tr>
  <tr><td>Club path, all shots</td><td class="num">&minus;8.6&deg; to +37.1&deg;</td><td class="num">a few degrees</td></tr>
</table></div>
<p>Attack angle returning a tightly clustered value near &minus;31&deg; on every shot regardless of the swing is a systematic artefact, not a measurement. The apparent azimuth swing is <b>three to ten times larger than the clubhead can physically produce</b>.</p>
<p>A plausible mechanism is scatterer migration across an extended, rotating target: the clubhead subtends about 4&deg; at 1.25&nbsp;m while rotating at roughly 1300&deg;/s, so the dominant scattering point moves between frames. <b>This is a hypothesis and has not been confirmed.</b> Note that the phase-span check deliberately does not unwrap, for documented reasons &mdash; unwrapping fabricated angles in earlier work.</p>

{heading("radar-isar", OUTLINE)}
<p>The radar cannot image the clubhead directly. Angular resolution is set by aperture, and the array is 19.3&nbsp;mm wide:</p>
<div class="scroll"><table>
  <tr><th>Quantity</th><th class="num">Value</th></tr>
  <tr><td>Wavelength (62 GHz)</td><td class="num">4.835 mm</td></tr>
  <tr><td>Range resolution</td><td class="num">46.9 mm</td></tr>
  <tr><td>Aperture (8 virtual elements at &lambda;/2)</td><td class="num">19.3 mm</td></tr>
  <tr><td>Beamwidth</td><td class="num">12.7&deg;</td></tr>
  <tr><td><b>Cross-range cell at 1.25 m</b></td><td class="num t-bad"><b>277 mm</b></td></tr>
  <tr><td>Clubhead, for comparison</td><td class="num">90 mm &mdash; 0.33 cells</td></tr>
</table></div>
<p>Clubhead, shaft and hands fall within a single angular cell. Additional pulses improve signal-to-noise and velocity resolution; they do not improve angular resolution.</p>
<p><b>A rotating target does synthesise an effective aperture</b> (inverse synthetic aperture radar). At 1300&deg;/s the head rotates 15.1&deg; across the 11.7&nbsp;ms tracked, giving <code>&lambda;/(2&Delta;&theta;)</code> = <b>9.2&nbsp;mm</b> of cross-range resolution &mdash; a 30-fold improvement, approximately ten cells across the head. Micro-Doppler analysis gives the same cell count, as it must.</p>
<p>Measured on the raw cube with range walk corrected, across <b>21 shots and 112 frames</b>:</p>
<div class="scroll"><table>
  <tr><th>Measurement</th><th class="num">Doppler bins</th></tr>
  <tr><td>Point-target floor, same estimator and window</td><td class="num">1.27</td></tr>
  <tr><td>Predicted from rotation alone (838 Hz)</td><td class="num">1.36</td></tr>
  <tr><td>Expected in quadrature</td><td class="num">1.86</td></tr>
  <tr><td><b>Measured clubhead median</b></td><td class="num t-ok"><b>1.95</b></td></tr>
</table></div>
<p>The implied toe-to-heel spread is <b>893&nbsp;Hz</b> against <b>838&nbsp;Hz</b> predicted. The clubhead return is measurably broader than a point target, by close to the amount its rotation should produce.</p>

<div class="panel flag">
  <h3>This is not yet evidence of rotation</h3>
  <p>The discriminating test is whether the spread scales with club speed. It returned a negative correlation, but the test has <b>no statistical power on this data</b>: the predicted effect across the full speed range is <b>0.154 bins</b> against an observed scatter of <b>0.490 bins</b>, and club type is perfectly confounded with speed (7-iron 37.4&ndash;38.8&nbsp;m/s, 9-iron 34.6&ndash;36.4&nbsp;m/s, no overlap). Additionally, <b>23&nbsp;% of frames return a width below the point-target floor</b>, which is unphysical and indicates a noisy estimator at 12 samples.</p>
  <p>Two measurements would resolve it: a session spanning a wide club-speed range, and a positive control on the ball, whose spin predicts roughly 8 bins of spread. Neither has been run.</p>
</div>

<p>If the rotation signal is real, the useful output is not an image. <b>Focusing an ISAR image requires estimating the target's rotation rate and axis</b> &mdash; the two parameters the pose fit currently leaves free.</p>
"""


def _comparators() -> str:
    return f"""
{heading("comparators", OUTLINE)}
<p>Two commercial systems solve this problem behind the ball, both with less capable cameras than ours.</p>
<div class="scroll"><table>
  <tr><th>System</th><th>Camera</th><th>Illumination</th><th>Markerless impact location</th></tr>
  <tr><td><b>Trackman 4</b></td><td>720p @ 60 fps</td><td>ambient, 700&ndash;800 lux</td><td class="t-ok">yes</td></tr>
  <tr><td><b>Mevo Gen 2</b></td><td>single phone-class module</td><td>ambient, 300 lux minimum</td><td class="t-ok">yes</td></tr>
  <tr><td><b>OpenFlight</b></td><td>468 fps</td><td>ambient</td><td class="t-bad">not yet</td></tr>
</table></div>
<p>The relevant difference is architectural rather than optical. Trackman's approach fuses the camera with radar that supplies kinematics and timing at 40&nbsp;kHz; at 60 fps the clubhead travels roughly 0.7&nbsp;m between frames, so the camera cannot track impact independently and is not required to. Impact location is a product of the fusion, not of frame rate.</p>
<p>OpenFlight has the ingredients: <b>8&times; Trackman&nbsp;4's frame rate</b>, impact timing to approximately 33&nbsp;&micro;s, and radar kinematics from two devices. What is missing is the fusion model &mdash; and, as &sect;{_num("pose-seg")} shows, a camera term whose noise floor is currently around ten degrees per pixel.</p>
<p class="sub">Comparator set is Trackman 4, Full Swing KIT and Mevo Gen 2. Trackman iO is excluded: it is ceiling-mounted, and its frame rate is the price of markerless spin from overhead rather than a behind-ball figure.</p>
"""


def _recommendations() -> str:
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


def _reproduce() -> str:
    return f"""
{heading("reproduce", OUTLINE)}
<p>All analysis in this report is reproducible from the repository. Scripts live in <code>research/silhouette_poc/falsification/</code>; start with its <code>README.md</code>.</p>
<div class="scroll"><table>
  <tr><th>Result</th><th>Script</th></tr>
  <tr><td>Silhouette information limits, per-axis</td><td><code>pose_landscape.py</code></td></tr>
  <tr><td>Metric comparison and refit</td><td><code>test_fusion_chamfer.py</code></td></tr>
  <tr><td>Range model test</td><td><code>test_radar_range_ramp.py</code></td></tr>
  <tr><td>Doppler width / ISAR assessment</td><td><code>test_isar_doppler_width.py</code></td></tr>
  <tr><td>Acoustic trigger timing</td><td><code>src/openflight/acoustic.py</code>, <code>tests/test_acoustic.py</code></td></tr>
  <tr><td>Delivered loft / face angle / lie</td><td><code>replay/club_angles.py</code>, <code>tests/test_club_angles.py</code></td></tr>
  <tr><td>Scoring primitives and their unit tests</td><td><code>replay/pose_scores.py</code>, <code>tests/test_pose_scores.py</code></td></tr>
</table></div>
<p>Scripts resolve the capture export via <code>OPENFLIGHT_SESSION</code>, a <code>--session</code> argument, or conventional paths, and fail with an actionable message if none is found. The club mesh is not redistributed; it is available from GrabCAD and is used for research only.</p>
<p><b>Contributing a capture is the most useful help.</b> A session with a driver and a wedge alongside the irons, at 1280&times;800 1:1, with a lux reading at the ball, would unblock three separate items in Tier 3 at once.</p>
"""


def _appendix() -> str:
    return f"""
{heading("appendix", OUTLINE)}
<p>Figures published in earlier versions of this work that were subsequently found to be wrong. They are listed because some were circulated, and because the failure modes recur.</p>
<div class="scroll"><table>
  <tr><th>Claim as published</th><th>Correction</th></tr>
  <tr><td>Measured loft 17.5&deg;</td><td><b>33.10&deg;.</b> The detector anchored to the cavity rim on the reverse of the club.</td></tr>
  <tr><td>The mesh has a 62&nbsp;mm shaft stub</td><td>It has <b>no shaft</b>. The feature measures 63.8&nbsp;mm &times; 12.9&ndash;17.5&nbsp;mm and is the hosel and ferrule.</td></tr>
  <tr><td>Free-depth fits at 1180&ndash;1336&nbsp;mm are errors of &minus;245 to &minus;401&nbsp;mm against the tape</td><td>Those ranges lie <b>inside</b> the clubhead's physical range during the fitted frames. The fit was tracking the club; pinning it to 1581&nbsp;mm moved it onto the ball. See &sect;{_num("pose-range")}.</td></tr>
  <tr><td>Representative fit quality, IoU 0.636</td><td><b>Not reproducible</b> by any code in the repository &mdash; the committed tracker returns 0.292 on that shot, a careful rebuild 0.452.</td></tr>
  <tr><td>The trigger lags impact by 2.11 frames &mdash; then retracted in favour of 6.0 frames</td><td><b>The retraction was the error.</b> 2.11 frames is correct and equals the acoustic time of flight over 1.575&nbsp;m. Confirmed by the ball track (71.89 &plusmn; 0.77, n=20) and by the physics, agreeing to 0.04 frames. The 6.0 figure came from misreading a render.</td></tr>
  <tr><td>Production carries a ~4.6&nbsp;ms impact-timing bias</td><td><b>Withdrawn.</b> Both production measurement paths derive impact from the data, not the trigger.</td></tr>
  <tr><td>Field of view 2.17&nbsp;m in the current mode</td><td><b>1.08&nbsp;m.</b> The calculation used the full sensor width where the capture reads half.</td></tr>
  <tr><td>A 6&nbsp;mm lens yields about one degree of face angle</td><td>Did not survive testing on real segmented edges, which improved by <b>0.78&times;</b> rather than the projected 2&times;.</td></tr>
  <tr><td><code>iwr_club_path_club_range_m</code> is the clubhead's range</td><td>It is the range at the <b>start of the radar track</b>, roughly 5.5 frames before impact.</td></tr>
  <tr><td>Radar club path and attack angle are exactly equal and opposite &mdash; a degeneracy</td><td>True on one shot only. Across 22 shots the sum ranges +7.8&deg; to &minus;42.1&deg;.</td></tr>
</table></div>
<div class="panel">
  <h3>Common cause</h3>
  <p>All but one of the above came from generalising a single shot, a single measurement, or a geometric assumption that was never checked against the mesh or the data. The corrections came from cross-set checks and from rendering the thing in question and looking at it. Both are cheap; neither was applied first.</p>
</div>
"""


def _num(anchor: str) -> str:
    """Section number for cross-references, so they cannot drift."""
    number = 0
    for a, _title, level in OUTLINE:
        if level == 2:
            number += 1
        if a == anchor:
            return f"{number:02d}"
    raise KeyError(anchor)


def main() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    figures = extract_figures(source)
    html = build(figures)
    TARGET.write_text(html, encoding="utf-8")
    size_mb = len(html.encode()) / 1e6
    print(f"wrote {TARGET}  ({size_mb:.2f} MB, was 10.0 MB)")
    print(f"figures carried over: {len(figures)} of 16")
    print(
        f"sections: {sum(1 for _a, _t, lvl in OUTLINE if lvl == 2)} "
        f"top-level, {sum(1 for _a, _t, lvl in OUTLINE if lvl == 3)} sub"
    )


if __name__ == "__main__":
    main()
