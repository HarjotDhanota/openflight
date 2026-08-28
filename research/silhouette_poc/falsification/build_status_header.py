"""Add a current-status block to the top of the page, and fix stale claims.

The page is a chronological narrative with corrections applied in place, which
is honest but leaves a newcomer to reconstruct the bottom line from 19,000
words across 32 sections. This adds an orientation block after the lede.

It also repairs three claims that later sections contradict:
  * section 07 tells the reader to re-run at 997 us and 0.33 px/mm. Both are
    wrong: 997 us is a known-bad operating point and the measured plate scale
    is 0.295 px/mm.
  * section 10 says "position and scale are solved". Section 11f measured the
    scale degree of freedom reversing sign on 67 % of consecutive frames and
    settling 245-401 mm short.
  * section 10d's state table quotes 349 frames at IoU 0.633 from a tracker
    that no longer exists, and IoU is now known to be the wrong metric.
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

STATUS = """
<div class="panel" id="status">
  <h3>Where this stands, as of the latest capture</h3>
  <p>What follows is a working log, written in the order things were found and corrected in place rather than tidied afterwards. If you only read one part, read this.</p>
  <p><b>Measured and holding, on 21 shots.</b> Ball detection 21 of 22 with no mis-detections. Camera and radar independently locate impact and agree to 0.66 frames. The camera is level to <b>0.185&deg;</b> and the ball sits 163&nbsp;mm below the lens, both measured rather than assumed. The 7-iron/9-iron launch gap is <b>real</b> &mdash; an independent reconstruction gives +2.91&deg; mean against the radar estimator&rsquo;s +2.59&deg;, and it survives a 13&deg; swing in assumed camera pitch.</p>
  <p><b>Not measurements yet.</b> Face angle, dynamic loft and impact location are all model-dependent inferences, not readings. Radar club path and attack angle are rejected on <b>21 of 21</b> shots. Camera and radar disagree by about <b>5&deg; in both axes</b> and nothing in the current data can arbitrate. The 3D model&rsquo;s frame is anchored to the <em>back</em> of the club by a detector bug, so every dynamic-loft figure derived from it inherits that.</p>
  <p><b>Please do not quote.</b> Any accuracy figure for impact location &mdash; none exists against truth, only against the system&rsquo;s own internal consistency. Silhouette overlap (IoU) as a measure of fit quality: it is <em>inversely</em> related to pose correctness here, measured across three arms over identical masks. And the &ldquo;a 6&nbsp;mm lens gives about one degree of face angle&rdquo; projection in &sect;11i, which did not survive testing on real segmented edges (&sect;11j).</p>
  <p><b>The single most useful next measurement</b> is not another estimator. It is a target at a tape-known position visible to both sensors, which would close the 5&deg; disagreement, plus a lux and exposure ladder in the actual bay, which decides whether the optical route is a one-degree instrument or a four-degree one.</p>
  <p style="font-size:14.6px;color:var(--ink-soft)">Code and full write-ups live in the repository under <code>research/silhouette_poc/falsification/</code> &mdash; start with its README, then <code>docs/superpowers/specs/2026-08-26-codex-handoff.md</code>, which opens with the seven claims published here and later retracted.</p>
</div>
"""


def main() -> None:
    s = PAGE.read_text(encoding="utf-8")
    n = 0

    def sub(old: str, new: str) -> None:
        nonlocal s, n
        assert old in s, f"NOT FOUND: {old[:70]!r}"
        assert s.count(old) == 1, f"AMBIGUOUS: {old[:60]!r}"
        s = s.replace(old, new, 1)
        n += 1

    # 1. orientation block, immediately before section 01
    anchor = '<h2><span class="secnum">01</span>'
    assert '<div class="panel" id="status">' not in s, "status block already present"
    sub(anchor, STATUS.strip() + "\n\n" + anchor)

    # 2. section 07 points the reader at a known-bad operating point
    sub(
        "Both are wrong for this hardware. Until the evaluation is re-run at 997&nbsp;&micro;s "
        "and 0.33&nbsp;px/mm &mdash; and with frame rate swept rather than assumed &mdash; there is "
        "<b>no validated accuracy figure</b> for impact location on real hardware. Anyone citing "
        "one from an earlier page should stop.",
        "Both are wrong for this hardware. The measured values are <b>247&ndash;298&nbsp;&micro;s "
        "and 0.295&nbsp;px/mm</b> &mdash; an earlier version of this paragraph said to re-run at "
        "997&nbsp;&micro;s and 0.33&nbsp;px/mm, which would have evaluated a <em>known-bad</em> "
        "operating point: 500&nbsp;&micro;s clipped 99.8&nbsp;% of the frame, and longer is worse. "
        "Re-runs should sweep <b>blur</b> as well as brightness, because the two pull in opposite "
        "directions (&sect;11i). There is still <b>no validated accuracy figure</b> for impact "
        "location on real hardware, and anyone citing one from an earlier page should stop.",
    )

    # 3. section 10 asserts scale is solved; 11f measured otherwise
    sub(
        "That is the honest summary of the whole project in one image. <b>Position and scale are "
        "solved. Orientation is not.</b> The outline looks right because the fit is finding a "
        "clubhead-shaped thing in the right place at the right size, which it does well. The "
        "angles printed beside it are the part nobody should yet believe.",
        "That is the honest summary of the whole project in one image. <b>Position looks right and "
        "orientation does not.</b> The outline reads correctly because the fit is finding a "
        "clubhead-shaped thing in roughly the right place, which it does well. The angles printed "
        "beside it are the part nobody should yet believe.<br><br>"
        "<em>Later correction:</em> the words here used to be &ldquo;position and scale are "
        "solved&rdquo;. <b>Scale is not solved.</b> &sect;11f measured the depth parameter "
        "reversing direction on 67&nbsp;% of consecutive frames and settling 245&ndash;401&nbsp;mm "
        "short of where the club physically is. The projection is correct perspective and the "
        "parameter exists &mdash; it simply carries no information, and was absorbing noise.",
    )

    # 4. section 10d state table
    sub(
        '<tr><td>Mesh position and scale</td><td class="t-ok">349 of 349 frames, '
        "IoU 0.633</td></tr>",
        '<tr><td>Mesh position and scale</td><td class="t-bad">superseded &mdash; that tracker '
        "no longer exists, and IoU turned out to be inversely related to pose correctness "
        "(&sect;11f)</td></tr>",
    )

    PAGE.write_text(s, encoding="utf-8")
    print(f"{n} edits applied to {PAGE}")


if __name__ == "__main__":
    main()
