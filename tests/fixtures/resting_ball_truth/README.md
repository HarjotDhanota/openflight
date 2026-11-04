# Resting-ball diameter truth (C26b, 3 Nov 2026)

31 resting balls: 27 from Oct 24 (Harjot-outdoor-test-1, 1280x800 at 120 fps; sun, mixed and dusk) and
the 4 Oct 27 shots (harjot-outdoor-test-2, 640x400). crops.npz holds each ball's resting still (median of
the pre-impact frames), uint8, cropped round the finder's centre; crops.json gives each crop's origin in
the frame and the origin of the labelling crop (the labels' coordinates).

- labeller_b.json: a fresh Opus subagent labelling blind (it saw only neutral 8x/12x renders with a pixel
  grid, no detector circle and no shot name).
- labeller_a.json: the implementing agent. It labelled AFTER labeller B's report had arrived in its context,
  so its labels are not fully independent of B's.
- truth.json: the width at the widest row (outer limb), the two labellers within 1.5 px on all 31;
  truth_d is their mean. The vertical axis is not used: every bottom limb sits in the contact shadow.
- split.json: the design / held-out split, fixed before any method was scored. Lighting class from the
  still itself (the ball cap's 99th percentile inside the finder circle: sun >= 250, dusk <= 180, else
  mixed; the Oct 27 640x400 balls their own class); within each class, ordered by session and shot,
  alternate design / held-out. design (17): dusk oct24-13,16,18,20,22,24,26,28,30; mixed oct24-06,09,11,14;
  oct27-01,03; sun oct24-02,04. held-out (14): dusk oct24-15,17,19,21,23,25,27,29; mixed oct24-07,10,12;
  oct27-02,04; sun oct24-03. Oct 27 shot 1 (the 24.0 +- 0.5 pin) is in the design half.
- finder.json: the existing finder's diameter and rim support on each ball (9e69a79f + C26b support).
  Held-out: 36 % within 1.5 px, mean +3.87, worst 16.8 (the numbers a future method must beat;
  tests/test_resting_ball_diameter_support.py records them). proto2 (first strong fall per ray, robust
  circle) on the same held-out half: 36 %, +1.47, worst 4.5, and 22.5 px on Oct 27 shot 1 (breaks the pin).
