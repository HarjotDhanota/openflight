"""A frame-by-frame viewer for what the fusion system currently produces.

Built from `render_fusion_view.py`'s output, which draws only model output --
the mesh's own projection, the mask the fit consumed, and the ball tracker's
own circle. Nothing is nudged to look better, so where the outlines disagree,
the fit disagrees.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

RENDERS = Path(__file__).with_name("renders") / "fusion_view"
OUT_DIR = Path(
    os.environ.get("OPENFLIGHT_PAGE_OUT", Path(__file__).resolve().parent / "page")
)
TARGET = OUT_DIR / "openflight-fusion-status.html"

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
  font-family:"IBM Plex Sans",system-ui,sans-serif;font-size:16px;line-height:1.6}
.wrap{max-width:1020px;margin:0 auto;padding:44px 22px 90px}
h1{font-family:"IBM Plex Sans Condensed",sans-serif;font-weight:700;
  font-size:clamp(27px,4.4vw,38px);line-height:1.1;margin:0 0 10px;text-wrap:balance}
h2{font-family:"IBM Plex Sans Condensed",sans-serif;font-weight:700;font-size:22px;
  margin:52px 0 6px;text-wrap:balance}
p{margin:12px 0;max-width:72ch}
.lede{color:var(--ink-soft);max-width:70ch;margin:0 0 8px;font-size:17px}
.meta{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--ink-soft);
  letter-spacing:.04em;margin:0 0 24px;text-transform:uppercase}
a{color:var(--cam)}
code{font-family:"IBM Plex Mono",monospace;font-size:.87em;background:var(--code);
  padding:1px 6px;border-radius:4px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:18px 22px;margin:22px 0}
.panel.flag{border-left:3px solid var(--bad)}
.panel.ok{border-left:3px solid var(--good)}
.panel.warn{border-left:3px solid var(--warn)}
.panel h3{margin:0 0 8px;font-size:16px;font-weight:600}
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
td.num{text-align:right;font-variant-numeric:tabular-nums;
  font-family:"IBM Plex Mono",monospace;font-size:13.5px;white-space:nowrap}
.t-bad{color:var(--bad);font-weight:600}
.t-ok{color:var(--good);font-weight:600}
.player{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:16px;margin:22px 0}
.stage{background:#0d0f0d;border-radius:8px;overflow:hidden;line-height:0}
.stage img{width:100%;height:auto;display:block;image-rendering:pixelated}
.ctl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px}
button{font:inherit;font-size:14px;padding:7px 14px;border-radius:7px;cursor:pointer;
  border:1px solid var(--line);background:var(--surface);color:var(--ink)}
button.pri{background:var(--cam);border-color:var(--cam);color:#fff;font-weight:600}
button:hover{border-color:var(--cam)}
button:focus-visible{outline:2px solid var(--cam);outline-offset:2px}
input[type=range]{flex:1;min-width:170px;accent-color:var(--cam)}
.read{font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--ink-soft);
  margin-top:10px;letter-spacing:.02em}
.key{display:flex;flex-wrap:wrap;gap:16px;margin:10px 0 0;font-size:13.5px;
  color:var(--ink-soft)}
.key span{display:flex;align-items:center;gap:7px}
.sw{width:15px;height:3px;border-radius:2px;display:inline-block}
"""

JS = """
(function(){
  var shots = window.__SHOTS__ || [];
  shots.forEach(function(shot, si){
    var root = document.getElementById('player-' + si);
    if(!root) return;
    var img = root.querySelector('img');
    var range = root.querySelector('input[type=range]');
    var play = root.querySelector('.play');
    var read = root.querySelector('.read');
    var i = 0, timer = null;
    function show(n){
      // Round: the impact frame is fractional, so indices derived from it are
      // too, and png[4.44] is undefined -- which renders as a broken image
      // rather than an error anyone would notice.
      i = Math.max(0, Math.min(shot.png.length - 1, Math.round(n)));
      if(!shot.png[i]) return;
      img.src = 'data:image/png;base64,' + shot.png[i];
      range.value = i;
      var f = shot.first + i;
      var fitted = shot.fitted.indexOf(f) >= 0;
      var post = f > shot.impact;
      read.textContent = 'frame ' + f + '  ·  ' +
        (post ? 'after impact' : 'before impact') + '  ·  ' +
        (fitted ? (post ? 'used by the fit — should not be' : 'used by the fit')
                : 'not used by the fit');
    }
    function stop(){ if(timer){ clearInterval(timer); timer = null; play.textContent = 'Play'; } }
    play.addEventListener('click', function(){
      if(timer){ stop(); return; }
      play.textContent = 'Pause';
      timer = setInterval(function(){
        if(i >= shot.png.length - 1){ show(0); } else { show(i + 1); }
      }, 260);
    });
    root.querySelector('.prev').addEventListener('click', function(){ stop(); show(i - 1); });
    root.querySelector('.next').addEventListener('click', function(){ stop(); show(i + 1); });
    range.addEventListener('input', function(){ stop(); show(parseInt(range.value, 10)); });
    range.max = shot.png.length - 1;
    show(Math.round(shot.impact - shot.first) - 2);
  });
})();
"""


def player(index: int, shot: dict) -> str:
    angles = shot["delivered"]
    verdict = (
        '<span class="t-ok">inside the physical envelope</span>'
        if shot["in_envelope"]
        else '<span class="t-bad">outside the physical envelope</span>'
    )
    post = shot.get("post_impact_fitted") or []
    post_note = (
        f"<p><b>Defect visible here:</b> frames {', '.join(str(f) for f in post)} are "
        f"after contact and the fit used them anyway. A rigid-rotation model does not "
        f"describe a club that has already struck the ball.</p>"
        if post
        else ""
    )
    return f"""
<div class="player" id="player-{index}">
  <div class="stage"><img alt="Shot {shot["shot"]} frame with model overlays"></div>
  <div class="ctl">
    <button class="pri play">Play</button>
    <button class="prev">&larr;</button>
    <button class="next">&rarr;</button>
    <input type="range" min="0" value="0" aria-label="frame">
  </div>
  <div class="read"></div>
  <div class="key">
    <span><i class="sw" style="background:#ff8c3c"></i>model projection</span>
    <span><i class="sw" style="background:#3cc8eb"></i>observed silhouette</span>
    <span><i class="sw" style="background:#8ceb6e"></i>ball</span>
  </div>
</div>
<div class="panel {"ok" if shot["in_envelope"] else "flag"}">
  <h3>Shot {shot["shot"]} &mdash; {shot["club"]}</h3>
  <p>Recovered delivery: <b>dynamic loft {angles["dynamic_loft_deg"]:+.1f}&deg;</b>,
     <b>face angle {angles["face_angle_deg"]:+.1f}&deg;</b>,
     <b>lie {angles["lie_deg"]:.1f}&deg;</b> &mdash; {verdict}.
     For reference the club's own geometry is 33.1&deg; loft, 61.2&deg; lie.</p>
  <p>Clubhead range swept <b>{shot["range_span_mm"][0]:.0f}&nbsp;mm &rarr;
     {shot["range_span_mm"][1]:.0f}&nbsp;mm</b> across the fitted window, from the radar.
     Fused velocity magnitude is <b>{shot["camera_speed_ratio"]:.2f}&times;</b> the radar's
     independently measured club speed.</p>
  <p><b>Pre-impact clubhead masks available: {len(shot.get("pre_impact_masks", []))}.</b>
     The fit has four free parameters.</p>
  {post_note}
</div>
"""


def main() -> None:
    manifest = json.loads((RENDERS / "manifest.json").read_text(encoding="utf-8"))
    assert manifest, "no rendered shots"
    payload = [
        {
            "shot": s["shot"],
            "first": s["frames"][0],
            "impact": s["impact_frame"],
            "fitted": s["fitted_frames"],
            "png": s["png_base64"],
        }
        for s in manifest
    ]
    body = "\n".join(player(i, s) for i, s in enumerate(manifest))
    html = f"""<meta charset="utf-8">
<title>Fusion Status</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@600;700&amp;family=IBM+Plex+Sans:wght@400;500;600&amp;family=IBM+Plex+Mono:wght@400;500&amp;display=swap">
<style>{CSS}</style>
<div class="wrap">
<h1>What the fusion system currently produces</h1>
<p class="meta">OpenFlight &middot; session 20260825_181734 &middot; rendered 2026-08-27</p>
<p class="lede">Every outline below is a model's own output drawn over the real frames &mdash;
the mesh projected through the camera model, the mask the fit consumed, and the ball
tracker's own circle. Nothing is padded or adjusted to look better.</p>

<div class="panel flag">
  <h3>Correction &mdash; this page was rebuilt after a timing error</h3>
  <p>An earlier version placed contact at frame&nbsp;68 on every shot, from an assumed
  6.0-frame trigger lag. <b>That was wrong by 3.89 frames.</b> Contact precedes the trigger
  by the sound's travel time from the ball to the unit: <b>4.59&nbsp;ms = 2.11&nbsp;frames</b>
  here, and it varies per installation. The acoustic model and the measured ball departure
  agree to <b>0.04 frames</b> (71.85 vs 71.89&nbsp;&plusmn;&nbsp;0.77 across 20 shots).</p>
  <p>Two claims made from the wrong constant are <b>withdrawn</b>: that the fit was
  consuming post-impact frames (with the correct impact, none of the fitted frames are
  post-impact), and that the median shot offers only two usable pre-impact frames
  (the correct figure is three).</p>
</div>

<div class="panel warn">
  <h3>Read this before the video</h3>
  <p><b>The radar half works.</b> Clubhead range comes from the radar and the fused
  camera-plus-radar velocity matches the radar's independent club-speed measurement to
  within a few percent. That part is validated.</p>
  <p><b>The orientation half does not.</b> Neither shot below lands inside a generous
  physical envelope, and the reason is visible in the frames rather than hidden in a
  metric: <b>there are almost no observations to fit.</b></p>
</div>

<h2>The binding constraint, measured across all 21 shots</h2>
<p>The clubhead is only segmentable for a handful of frames before contact:</p>
<div class="scroll"><table>
  <tr><th>Pre-impact clubhead masks per shot</th><th class="num">Shots</th></tr>
  <tr><td>5 masks</td><td class="num">4 of 21</td></tr>
  <tr><td>4 masks</td><td class="num">2 of 21</td></tr>
  <tr><td>3 masks</td><td class="num">11 of 21</td></tr>
  <tr><td>2 masks</td><td class="num">3 of 21</td></tr>
  <tr><td>1 or 0 masks</td><td class="num t-bad">1 of 21</td></tr>
</table></div>
<p><b>The median shot offers three usable pre-impact frames. The pose fit has four free
parameters.</b> On fifteen of twenty-one shots there are fewer observations than unknowns,
so the problem is under-determined before any question of metric, noise or cue arises.
That is the single most important number on this page.</p>

{body}

<h2>What this means</h2>
<div class="panel">
  <h3>Three separate things, in order of severity</h3>
  <p><b>1. Not enough observations.</b> Three frames against four parameters, and fewer
  than four observations on fifteen of twenty-one shots. No estimator recovers a pose
  reliably from that, and no change to the objective function helps.</p>
  <p><b>2. The model projects smaller than the observation.</b> Visible in every frame:
  the orange outline sits inside the cyan one. Range is now radar-derived rather than
  pinned to the ball, which removed a systematic error, but a residual scale gap remains.</p>
  <p><b>3. Face angle is the least observable axis.</b> Five degrees of face angle change
  the projected silhouette by zero pixels, and one pixel of segmentation error is worth
  about ten degrees. Both shots below fail primarily on face angle.</p>
</div>
<p>The useful next step is not a better fit. It is <b>more usable frames</b> &mdash; a
longer segmentable approach, which means better separation of the clubhead from the mat
and the shaft, and more pixels on the target.</p>
</div>
<script>window.__SHOTS__ = {json.dumps(payload)};</script>
<script>{JS}</script>
"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(html, encoding="utf-8")
    print(
        f"wrote {TARGET} ({len(html.encode()) / 1e6:.2f} MB, "
        f"{sum(len(s['png']) for s in payload)} frames)"
    )


if __name__ == "__main__":
    main()
