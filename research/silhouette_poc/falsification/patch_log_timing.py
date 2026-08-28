"""Flag the impact-timing correction at the top of the working log.

The log is a chronological record and its wrong turns are part of its value, so
the original text stays. But a reader arriving at the top must not spend
nineteen thousand words before learning that the trigger-lag constant which
anchors the range model and every pose fit was wrong.
"""

from __future__ import annotations

import os
from pathlib import Path

PAGE = Path(os.environ["OPENFLIGHT_PAGE_OUT"]) / "openflight-impact-location.html"
s = PAGE.read_text(encoding="utf-8")


def sub(old: str, new: str) -> None:
    global s
    assert old in s, f"NOT FOUND: {old[:70]!r}"
    assert s.count(old) == 1, f"AMBIGUOUS ({s.count(old)}): {old[:70]!r}"
    s = s.replace(old, new, 1)


if 'id="timing-correction"' in s:
    raise SystemExit("already flagged")

BANNER = """
<div class="panel flag" id="timing-correction">
  <h3>Correction, 2026-08-27 &mdash; the trigger-lag constant was wrong</h3>
  <p>This page states that the acoustic trigger fires <b>6.0&nbsp;frames</b> after contact, putting impact at frame&nbsp;68 on every shot. <b>That is wrong by 3.89 frames.</b></p>
  <p>Contact precedes the trigger by the sound's travel time from the ball to the unit: <b>1.575&nbsp;m &divide; 343&nbsp;m/s = 4.59&nbsp;ms = 2.11&nbsp;frames</b> at 468&nbsp;fps. Two independent routes agree to <b>0.04 frames</b> &mdash; the acoustic model gives frame <b>71.85</b>, and measuring the ball's departure across 20 shots gives <b>71.89&nbsp;&plusmn;&nbsp;0.77</b>. Per-shot values run 70.65 to 73.64; it is not a constant, and it scales with how far the unit sits from the ball.</p>
  <p><b>How the error happened:</b> a render was misread. The clubhead is 27&nbsp;px wide and sits adjacent to the ball for two or three frames before it strikes, so &ldquo;the head reaches the ball&rdquo; was taken for contact. Contact is when the <b>ball starts moving</b>. The ball-track estimate this page warns against was correct; the warning was the error.</p>
  <p><b>What this supersedes:</b> the clubhead range model in &sect;14 was anchored ~270&nbsp;mm off, and the pose fits built on it are superseded. The &ldquo;post-impact frames were fitted&rdquo; defect reported later was an artefact of the same wrong anchor.</p>
  <p><b>What it does not affect:</b> production. <code>iwr6843/shot.py:impact_time_s</code> back-extrapolates the ball's own range walk rather than trusting the trigger, and <code>camera/club_delivery.py</code> detects the impact frame directly, using the trigger only as a wide plausibility gate. The defect was confined to the research scripts. Now shipped as <code>src/openflight/acoustic.py</code>.</p>
</div>
"""

sub(
    '<div class="panel" id="status">',
    BANNER.strip() + '\n\n<div class="panel" id="status">',
)

sub(
    "Anchoring the ramp with the ball-track impact estimate (~frame 71.8) instead of the "
    "documented trigger-minus-6.0-frames (68.0) mis-anchors it by <b>~270&nbsp;mm</b>",
    "<b>[Reversed &mdash; see the correction at the top of this page. The ball-track "
    "estimate near frame 71.8 is RIGHT and the 6.0-frame constant is wrong; this paragraph "
    "has the two the wrong way round.]</b> Anchoring the ramp with the ball-track impact "
    "estimate (~frame 71.8) instead of the trigger-minus-6.0-frames (68.0) mis-anchors it "
    "by <b>~270&nbsp;mm</b>",
)

PAGE.write_text(s, encoding="utf-8")
print("working log flagged")
