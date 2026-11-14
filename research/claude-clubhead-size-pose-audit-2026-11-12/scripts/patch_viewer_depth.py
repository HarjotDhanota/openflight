"""One-off: make the viewer use the depth-consistent poses (results/final-poses.json) for each contact instant.
Every replacement must match exactly once. Usage: patch_viewer_depth.py"""
from common import AUDIT

p = AUDIT / "viewer3d" / "template.html"
t = p.read_text(encoding="utf-8")
edits = [
    ("    const base = state.pose === 'contact' ? SCENE.contact_origins[state.instant] : pose.origin;",
     "    const base = SCENE.poses_by_instant[state.instant][state.pose];"),
    ("    let originC = add(SCENE.contact_origins[state.instant], [1, 0, 0], +sliders.dx.value);",
     "    let originC = add(SCENE.poses_by_instant[state.instant].contact, [1, 0, 0], +sliders.dx.value);"),
    ("    sliders.dx.value = 0; sliders.dy.value = 0; sliders.size.value = SCENE.size;",
     "    sliders.dx.value = 0; sliders.dy.value = 0; sliders.size.value = SCENE.poses_by_instant[state.instant].size;"),
    ("    state.instant = b.dataset.instant;\n",
     "    state.instant = b.dataset.instant;\n    sliders.size.value = SCENE.poses_by_instant[state.instant].size;\n"),
    ("        <li>Position and the 0.92 size come from the pixels: the shaft line, the head's top edge and your trace.</li>",
     "        <li>Where the club sits across the view comes from the pixels: the shaft line, the head's top edge and your trace.</li>\n"
     "        <li>The club's distance from the camera is not measured. It is set so the face touches the ball at contact, and moved by club speed times time for the other two frames. The size (0.93) follows from that distance.</li>"),
]
for old, new in edits:
    assert t.count(old) == 1, "not found exactly once: " + old[:80]
    t = t.replace(old, new)
p.write_text(t, encoding="utf-8")
print("applied", len(edits), "edits")
