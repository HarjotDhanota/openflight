"""One-off: add the ball after impact and the choice of contact instant to viewer3d/template.html.
Every replacement must match exactly once; the script stops otherwise. Usage: patch_viewer_ball.py"""
from common import AUDIT

p = AUDIT / "viewer3d" / "template.html"
t = p.read_text(encoding="utf-8")
edits = [
    # ---- moment buttons and the contact-instant choice
    ('<button class="btn" id="p-before" data-pose="before" aria-pressed="true">2.4 ms before</button>',
     '<button class="btn" id="p-before" data-pose="before" aria-pressed="true">Frame before</button>'),
    ('<button class="btn" id="p-after" data-pose="after" aria-pressed="false">1.2 ms after</button>',
     '<button class="btn" id="p-after" data-pose="after" aria-pressed="false">Frame after</button>'),
    ('        <p class="hint" id="pose-hint"></p>\n      </div>',
     '        <p class="hint" id="pose-hint"></p>\n'
     '        <div class="seg" role="group" aria-label="Which contact instant to use">\n'
     '          <button class="btn" id="t-track" data-instant="track" aria-pressed="true">Contact from the ball\'s track</button>\n'
     '          <button class="btn" id="t-app" data-instant="app" aria-pressed="false">App\'s contact instant</button>\n'
     '        </div>\n'
     '        <p class="hint" id="instant-hint"></p>\n      </div>'),
    # ---- state
    ("const state = { pose: 'before', view: 'camera' };", "const state = { pose: 'before', view: 'camera', instant: 'track' };"),
    # ---- flight ball objects, created once after the resting ball
    ("  ballMesh.position.copy(V(ball));\n  scene.add(ballMesh);",
     "  ballMesh.position.copy(V(ball));\n  scene.add(ballMesh);\n"
     "  const bfl = SCENE.ball_flight;\n"
     "  const flight = new THREE.Group();\n"
     "  const flyMat = new THREE.MeshStandardMaterial({ color: 0xf7f7f2, roughness: 0.5, transparent: true, opacity: 0.8 });\n"
     "  const fly44 = new THREE.Mesh(new THREE.SphereGeometry(R, 40, 28), flyMat);\n"
     "  fly44.position.copy(V(bfl.f44));\n"
     "  flight.add(fly44);\n"
     "  ['f45', 'f46'].forEach((k) => {\n"
     "    const g = new THREE.Mesh(new THREE.SphereGeometry(R, 24, 16), new THREE.MeshBasicMaterial({ color: 0xf7f7f2, wireframe: true, transparent: true, opacity: 0.35 }));\n"
     "    g.position.copy(V(bfl[k]));\n"
     "    flight.add(g);\n"
     "  });\n"
     "  flight.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([V(ball), V(bfl.f44), V(bfl.f45), V(bfl.f46)]), new THREE.LineBasicMaterial({ color: 0xffe27a })));\n"
     "  flight.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(bfl.f44_band.map(V)), new THREE.LineBasicMaterial({ color: 0xff8a3c })));\n"
     "  scene.add(flight);"),
    # ---- pose origin by contact instant
    ("    const pose = SCENE.poses[state.pose];\n    let origin = add(pose.origin, [1, 0, 0], +sliders.dx.value);",
     "    const pose = SCENE.poses[state.pose];\n    const base = state.pose === 'contact' ? SCENE.contact_origins[state.instant] : pose.origin;\n    let origin = add(base, [1, 0, 0], +sliders.dx.value);"),
    ("    let originC = add(SCENE.poses.contact.origin, [1, 0, 0], +sliders.dx.value);",
     "    let originC = add(SCENE.contact_origins[state.instant], [1, 0, 0], +sliders.dx.value);"),
    # ---- hints and visibility
    ("    const hints = { before: 'Frame 43. The model is fitted to your trace here.', contact: 'Between frames 43 and 44. The picture shown is frame 43, with the ball still at rest.', after: 'Frame 44. The ball has left; the model is placed from the shaft and the top edge.' };\n    el('pose-hint').textContent = hints[state.pose];",
     "    const tm = SCENE.timing[state.instant];\n"
     "    const hints = { before: 'Frame 43, ' + tm.before_ms.toFixed(2) + ' ms before contact. The model is fitted to your trace here.',\n"
     "      contact: 'Mid-contact, between frames 43 and 44. The picture shown is frame 43, with the ball still at rest.',\n"
     "      after: 'Frame 44, ' + tm.after_ms.toFixed(2) + ' ms after contact. The ball is drawn where its track puts it; the faint ball is where it rested.' };\n"
     "    el('pose-hint').textContent = hints[state.pose];\n"
     "    el('instant-hint').textContent = state.instant === 'track'\n"
     "      ? 'Mid-contact from the ball\\'s own flight over frames 44 to 46 and the radar speed: ' + Math.abs(SCENE.timing.track_minus_app_ms).toFixed(2) + ' ms earlier than the app\\'s.'\n"
     "      : 'The app\\'s instant, from the ball\\'s top edge on one frame and the radar launch model.';"),
    ("    ballMesh.visible = el('c-ball').checked;",
     "    ballMesh.visible = el('c-ball').checked;\n    ballMesh.material.opacity = state.pose === 'after' ? 0.16 : 0.55;\n    flight.visible = el('c-ball').checked && state.pose === 'after';"),
    # ---- instant buttons
    ("  Object.values(sliders).forEach((s) => s.addEventListener('input', build));",
     "  document.querySelectorAll('[data-instant]').forEach((b) => b.addEventListener('click', () => {\n"
     "    state.instant = b.dataset.instant;\n"
     "    document.querySelectorAll('[data-instant]').forEach((o) => o.setAttribute('aria-pressed', String(o === b)));\n"
     "    build();\n"
     "  }));\n"
     "  Object.values(sliders).forEach((s) => s.addEventListener('input', build));"),
    # ---- copy
    ('<label><input type="checkbox" id="c-ball" checked> Ball</label>', '<label><input type="checkbox" id="c-ball" checked> Ball and flight</label>'),
    ('<span><span class="dot normal"></span>arrow: where the face points</span>',
     '<span><span class="dot normal"></span>arrow: where the face points</span>\n    <span><span class="dot path"></span>ball\'s flight; wire balls are frames 45 and 46</span>'),
    ('.dot.trace { background: var(--trace); border-radius: 2px; height: 3px; width: 14px; }',
     '.dot.trace { background: var(--trace); border-radius: 2px; height: 3px; width: 14px; }\n.dot.path { background: var(--path); border-radius: 2px; height: 3px; width: 14px; }'),
    ('  --read: #1d9a4a; --mark: #c2267f; --app: #d9780a; --trace: #e23b3b; --normal: #2b7fd4;',
     '  --read: #1d9a4a; --mark: #c2267f; --app: #d9780a; --trace: #e23b3b; --normal: #2b7fd4; --path: #b8890a;'),
    ('        <li>The mat is drawn level through the ball\'s bottom; the camera is assumed level, so treat it as approximate.</li>',
     '        <li>The ball after impact is measured: its direction from the camera comes from its edge on frames 44 to 46 (fit within 0.4 px), its distance from that flight and the radar speed. The short orange line through it is the 6 mm either way I would allow in distance.</li>\n'
     '        <li>The mat is drawn level through the ball\'s bottom; the camera is assumed level, so treat it as approximate.</li>'),
]
for old, new in edits:
    assert t.count(old) == 1, "not found exactly once: " + old[:80]
    t = t.replace(old, new)
# the dark-theme blocks define the same token list twice: add --path to both
dark_old = "--read: #4fd07a; --mark: #f06bb4; --app: #f2a23c; --trace: #ff6b6b; --normal: #6fb1f2;"
assert t.count(dark_old) == 2
t = t.replace(dark_old, dark_old + " --path: #ffd35a;")
p.write_text(t, encoding="utf-8")
print("applied", len(edits) + 1, "edits")
