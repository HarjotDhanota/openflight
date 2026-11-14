"""Shot 1 impact point from the placed rigid 3-D club. RESEARCH READING, development shot, not validated.

Fixed before the numbers were read (no photo is read, nothing is tuned to a mark):
  model      club3d rigid club, size 0.919 (from the owner's trace on f43), angles as the application supplies them
  f43        2.44 ms before contact. Two anchors: AUTO (shaft line + top edge) and TRACE (the fit to the owner's trace).
  f44        1.18 ms after contact. Two independent placements:
               head edge   shaft line + the head's top edge, searched within +-8 px of the f43 model carried by the
                           ferrule's motion; an edge counts only with the dark cavity under it (the ball is in
                           flight just above the head on this frame)
               shaft only  shaft line + the ferrule at the hosel length this club showed on f43 (no head pixel used)
  contact    the application's contact instant, between f43 and f44: linear in time, or with the club slowed to 0.83
             after contact (the application's own assumption).
  reading    the ball's contact point (centre - r * face normal); its camera ray intersected with the model's face
             plane; mm on the club, toe-ward of the face centre and up from it.
  f42 is placed and reported but NOT used (first run: it does not fit the supplied angles; kept in the output).
Writes results/IMPACT-SHOT1.txt, figures/impact-shot1.png. Usage: impact1.py"""
import json
import math

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
from scipy.optimize import least_squares

import carry_lib as cl
import club3d
import diag_lib as dl
import geom
from common import AUDIT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


SHOT = 1
s = dl.setup(SHOT)
prep = s["prep"]
fit = json.loads((AUDIT / "results" / "fit-trace.json").read_text())["translation+size, 3-D hosel"]["params"]
SIZE = fit["size"]
trace = np.asarray(json.loads((AUDIT / "owner-trace" / "shot1-f43-trace.json").read_text())["polygon_native_px"])
times, t_c = prep["frame_time_ms"], prep["contact_ms"]
shape = s["frames"][43].shape
ball_px = np.array([prep["ball"]["x"], prep["ball"]["y"]])
r_px = prep["ball"]["diameter_px"] / 2.0
ANGLES = s["angles"]
SCENE = s["scene"]


def shaft_frame(angles, size=SIZE):
    toe, up, normal = geom.axes(*angles)
    direction = club3d.SHAFT_TUN[0] * toe + club3d.SHAFT_TUN[1] * up + club3d.SHAFT_TUN[2] * normal
    ground = size * (club3d.GROUND_TUN[0] * toe + club3d.GROUND_TUN[1] * up + club3d.GROUND_TUN[2] * normal)
    return direction, ground


def axis_residual(origin, obs, angles, shift_px=0.0):
    direction, ground = shaft_frame(angles)
    axis = SCENE.project(np.stack([origin + ground, origin + ground + 150.0 * direction]))
    a, b = obs["line"][0], obs["line"][1]
    fy = obs["ferrule"]["y"]
    rows = np.array([fy - 16.0, fy - 6.0, fy + 5.0])
    t = (rows - axis[0, 1]) / (axis[1, 1] - axis[0, 1])
    xm = axis[0, 0] + t * (axis[1, 0] - axis[0, 0])
    return (a + b * rows + shift_px - xm) / math.hypot(1.0, b)


def place_by_edge(f, obs, edge, angles):
    """Shaft line (across the shaft) + the head's top edge (along it). Returns origin, shaft rms px, edge rms px."""
    base = dl.origin_at(s, f)
    club = club3d.Club(SCENE, base, *s["sole"], shape)
    weight = math.sqrt(max(len(edge), 1) / 3.0)

    def res(p):
        _polys, _axis, lead = club.parts(*angles, p[0], p[1], SIZE)
        r_edge = np.array([e[1] - cl.top_at(lead, e[0]) for e in edge])
        return np.concatenate([axis_residual(base + np.array([p[0], p[1], 0.0]), obs, angles) * weight, np.where(np.isfinite(r_edge), r_edge, 5.0)])

    sol = least_squares(res, [0.0, 0.0], x_scale=5.0)
    r = res(sol.x)
    return base + np.array([sol.x[0], sol.x[1], 0.0]), float(np.sqrt(np.mean((r[:3] / weight) ** 2))), float(np.sqrt(np.mean(r[3:] ** 2)))


def ferrule_length(origin, obs, angles):
    """mm up the model's shaft axis from its ground point to the observed ferrule row: this club's own hosel length."""
    direction, ground = shaft_frame(angles)
    ts = np.linspace(20.0, 140.0, 4801)
    ys = SCENE.project(origin + ground + ts[:, None] * direction)[:, 1]
    return float(ts[int(np.argmin(np.abs(ys - obs["ferrule"]["y"])))])


def across_offset_px(origin, obs, angles):
    """Signed px from the observed shaft highlight line to the model's axis (model - observed), mean of three rows."""
    return float(-np.mean(axis_residual(origin, obs, angles)) * math.hypot(1.0, obs["line"][1]))


def place_by_ferrule(f, obs, angles, length, highlight_px=0.0):
    """Shaft only: the shaft line (across) and the ferrule at a known length up the axis (along). No head pixel."""
    base = dl.origin_at(s, f)
    direction, ground = shaft_frame(angles)

    def res(p):
        origin = base + np.array([p[0], p[1], 0.0])
        fer = SCENE.project(origin + ground + length * direction)[0]
        return np.concatenate([axis_residual(origin, obs, angles, shift_px=highlight_px), [fer[1] - obs["ferrule"]["y"]]])

    sol = least_squares(res, [0.0, 0.0], x_scale=5.0)
    return base + np.array([sol.x[0], sol.x[1], 0.0])


def model_lead(origin, angles):
    club = club3d.Club(SCENE, origin, *s["sole"], shape)
    _p, _axis, lead = club.parts(*angles, 0.0, 0.0, SIZE)
    return lead


def edge_window(frame, pred_lead, reach=8):
    """Top edge within +-reach px of a predicted outline: per column the strongest downward darkening that has the
    DARK CAVITY under it (the lower rim of a ball in flight has bright background under it and is refused)."""
    img = cv2.GaussianBlur(frame.astype(np.float32), (0, 0), 0.7)
    grad = img[1:, :] - img[:-1, :]
    toe = pred_lead[:, 0].max()
    inside = club3d.polygon_mask(pred_lead, frame.shape)[club3d.SS // 2::club3d.SS, club3d.SS // 2::club3d.SS]
    dark = float(np.percentile(img[inside], 5)) + 15.0
    out = []
    for x in range(int(toe) - 26, int(toe) - 3):
        y = cl.top_at(pred_lead, float(x))
        if not np.isfinite(y):
            continue
        lo, hi = int(round(y)) - reach, int(round(y)) + reach
        g = grad[lo:hi, x].copy()
        below = np.minimum(img[lo + 1:hi + 1, x], img[lo + 2:hi + 2, x])
        g[below > dark] = 0.0
        i = int(np.argmin(g))
        if g[i] < -6.0:
            out.append((x, lo + i + 0.5, float(g[i])))
    return np.asarray(out, dtype=float).reshape(-1, 3), dark


def reading(origin, angles):
    """Ball contact point in face coordinates (mm on the club); the plane's gap to the contact point along the normal."""
    toe, up, normal = geom.axes(*angles)
    contact = SCENE.ball - geom.BALL_RADIUS_MM * normal
    ray = contact / np.linalg.norm(contact)
    lam = float(origin @ normal) / float(ray @ normal)
    hit = lam * ray - origin
    return float(hit @ toe), float(hit @ up), float((contact - origin) @ normal)


NAMES = ("f44 by head edge, linear", "f44 by head edge, slowed 0.83", "f44 by shaft only, linear", "f44 by shaft only, slowed 0.83")


def run(angles, label, verbose=True):
    c = cl.carry(SHOT)
    A, B = c["A"], c["B"]
    C = dl.observe_frame(s, 44)
    o43, ra43, re43 = place_by_edge(43, B, c["edge"], angles)
    o43_trace = dl.origin_at(s, 43) + np.array([fit["dx"], fit["dy"], 0.0])
    d_fer = np.array([C["ferrule"]["x"] - B["ferrule"]["x"], C["ferrule"]["y"] - B["ferrule"]["y"]])
    out = {}
    for anchor, o43x in (("AUTO", o43), ("TRACE", o43_trace)):
        lead43 = model_lead(o43x, angles)
        edge44, dark = edge_window(s["frames"][44], lead43 + d_fer)
        # the highlight offset seen on f43 for this anchor is kept on f44 (same shaft, same light, 3.6 ms later)
        hl43 = across_offset_px(o43x, B, angles)
        hl44 = hl43 * o43x[2] / dl.origin_at(s, 44)[2]
        base44 = dl.origin_at(s, 44)
        club44 = club3d.Club(SCENE, base44, *s["sole"], shape)
        weight = math.sqrt(max(len(edge44), 1) / 3.0)

        def res(p, club44=club44, base44=base44, edge44=edge44, hl44=hl44, weight=weight):
            _polys, _axis, lead = club44.parts(*angles, p[0], p[1], SIZE)
            r_edge = np.array([e[1] - cl.top_at(lead, e[0]) for e in edge44])
            return np.concatenate([axis_residual(base44 + np.array([p[0], p[1], 0.0]), C, angles, shift_px=-hl44) * weight, np.where(np.isfinite(r_edge), r_edge, 5.0)])

        sol = least_squares(res, [0.0, 0.0], x_scale=5.0)
        r = res(sol.x)
        o44e = base44 + np.array([sol.x[0], sol.x[1], 0.0])
        length = ferrule_length(o43x, B, angles)
        o44f = place_by_ferrule(44, C, angles, length, highlight_px=-hl44)
        out[anchor] = {"o43": o43x, "o44e": o44e, "o44f": o44f, "edge44": edge44, "length": length, "hl43": hl43,
                       "ra44": float(np.sqrt(np.mean((r[:3] / weight) ** 2))), "re44": float(np.sqrt(np.mean(r[3:] ** 2))), "dark": dark}
    if verbose:
        say("%s: face %+.2f, loft %.2f, roll %+.2f deg; size %.3f" % (label, *angles, SIZE))
        say("   f43 AUTO: shaft residual %.2f px, top-edge residual %.2f px over %d columns. TRACE anchor is (%+.1f, %+.1f) mm from it." % (ra43, re43, len(c["edge"]), *(o43_trace - o43)[:2]))
        for anchor in ("AUTO", "TRACE"):
            q = out[anchor]
            pe, pf = SCENE.project(q["o44e"])[0], SCENE.project(q["o44f"])[0]
            say("   f44 %-5s head edge: %d columns with the cavity (< %.0f DN) under them, shaft residual %.2f px, edge residual %.2f px, face centre at (%.1f, %.1f) px | shaft only: hosel length %.1f mm, face centre at (%.1f, %.1f) px | the two differ by (%+.1f, %+.1f) px" % (
                anchor, len(q["edge44"]), q["dark"], q["ra44"], q["re44"], *pe, q["length"], *pf, *(pe - pf)))
        cols42 = np.arange(int(A["toe_x"]) - 26, int(A["toe_x"]) - 3)
        edge42 = cl.cavity_top(s["frames"][42], cols42, int(A["ferrule"]["y"]) - 14, int(round(A["bottom_y"])))
        _o42, ra42, re42 = place_by_edge(42, A, edge42, angles)
        say("   f42 NOT used: at the supplied angles its top edge misses the model by %.1f px rms (%d columns): 6 ms out the head is turned and lofted differently" % (re42, len(edge42)))
        say("   ferrule moved (%+.1f, %+.1f) px from f43 to f44" % (d_fer[0], d_fer[1]))
    t1, t2 = t_c - times[43], times[44] - t_c
    lin, slow = t1 / (t1 + t2), t1 / (t1 + 0.83 * t2)
    table = {}
    for anchor in ("AUTO", "TRACE"):
        q = out[anchor]
        for name, o44x, frac in ((NAMES[0], q["o44e"], lin), (NAMES[1], q["o44e"], slow), (NAMES[2], q["o44f"], lin), (NAMES[3], q["o44f"], slow)):
            origin = q["o43"] + (o44x - q["o43"]) * frac
            table[(anchor, name)] = (*reading(origin, angles), origin)
    return table, out


say("SHOT 1 IMPACT POINT ON THE PLACED 3-D CLUB (research reading; development shot; not validated)")
say("contact instant %.3f ms: f43 is %.3f ms before, f44 %.3f ms after; resting ball (%.2f, %.2f) px, radius %.2f px" % (t_c, t_c - times[43], times[44] - t_c, *ball_px, r_px))
table, info = run(ANGLES, "supplied angles")
toe_tip, le = SIZE * club3d.TOE_U, -SIZE * club3d.SOLE_V
say()
say("   anchor  f44 placement, interpolation               toe-ward of centre   up from centre   from the toe tip   above the leading edge   face-plane gap to the ball in depth")
for (anchor, name), (u, v, gap, _o) in table.items():
    say("   %-6s  %-40s  %+6.1f mm            %+6.1f mm        %5.1f mm           %5.1f mm                 %+6.1f mm" % (anchor, name, u, v, toe_tip - u, v + le, gap))
us = np.array([r[0] for r in table.values()])
vs = np.array([r[1] for r in table.values()])
say()
say("   all eight: toe-ward %+.1f .. %+.1f mm (median %+.1f); up %+.1f .. %+.1f mm (median %+.1f)" % (us.min(), us.max(), np.median(us), vs.min(), vs.max(), np.median(vs)))
tr = [k for k in table if k[0] == "TRACE"]
ut, vt = np.array([table[k][0] for k in tr]), np.array([table[k][1] for k in tr])
say("   trace anchor only (four): toe-ward %+.1f .. %+.1f mm; up %+.1f .. %+.1f mm" % (ut.min(), ut.max(), vt.min(), vt.max()))
say("   face centre on this size: %.1f mm from the toe tip, %.1f mm above the leading edge" % (toe_tip, le))
say()
say("SENSITIVITY (automatic anchor, f44 by head edge, slowed), everything re-placed from the pixels:")
key = ("AUTO", NAMES[1])
for df, dlo, dr, lab in ((0, 6.0, 0, "loft +6"), (0, -6.0, 0, "loft -6"), (6.0, 0, 0, "face +6"), (-6.0, 0, 0, "face -6"), (0, 0, 4.0, "roll +4"), (0, 0, -4.0, "roll -4")):
    tb, _i = run((ANGLES[0] + df, ANGLES[1] + dlo, ANGLES[2] + dr), lab, verbose=False)
    say("   %-8s moves the reading by %+5.1f mm toe-ward, %+5.1f mm up" % (lab, tb[key][0] - table[key][0], tb[key][1] - table[key][1]))
q = info["AUTO"]
for dt in (0.14, -0.14):
    t1, t2 = t_c + dt - times[43], times[44] - t_c - dt
    u, v, _g = reading(q["o43"] + (q["o44e"] - q["o43"]) * t1 / (t1 + 0.83 * t2), ANGLES)
    say("   contact %+.2f ms moves it by %+5.1f mm toe-ward, %+5.1f mm up" % (dt, u - table[key][0], v - table[key][1]))
say("   1 px of placement error on either frame is %.1f to %.1f mm" % (q["o43"][2] / SCENE.focal, q["o44e"][2] / SCENE.focal))
pl = s["state"]["placement"]
say()
say("FOR COMPARISON  application 0fa9870b: %+.1f mm toe-ward (+-%.1f), %+.1f mm up (+-%.1f).  Codex's contact-pose trial: about +36 toe-ward, +10 up." % (
    pl["impact_offset_mm"], pl["impact_offset_sigma_mm"], pl["impact_height_mm"], pl["impact_height_sigma_mm"]))
(AUDIT / "results" / "IMPACT-SHOT1.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "impact-shot1.json").write_text(json.dumps({
    "angles": list(ANGLES), "size": SIZE, "ball_mm": SCENE.ball.tolist(), "ball_radius_mm": geom.BALL_RADIUS_MM,
    "poses": {a: {"f43": info[a]["o43"].tolist(), "f44_head_edge": info[a]["o44e"].tolist(), "f44_shaft_only": info[a]["o44f"].tolist(),
                  "hosel_length_mm": info[a]["length"]} for a in ("AUTO", "TRACE")},
    "contact": {"%s|%s" % k: {"toe_mm": v[0], "up_mm": v[1], "origin": v[3].tolist()} for k, v in table.items()},
    "times_ms": {"f43": times[43] - t_c, "f44": times[44] - t_c}}, indent=1))

# ---------------------------------------------------------------- figure
fig, axs = plt.subplots(1, 4, figsize=(34, 8.2))
x0, x1, y0, y1 = 112, 212, 62, 122


def base_image(a, frame):
    a.imshow(frame[y0:y1, x0:x1], cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
    a.xaxis.set_major_locator(MultipleLocator(5))
    a.yaxis.set_major_locator(MultipleLocator(5))
    a.tick_params(labelsize=6)
    a.set_xlim(x0 - .5, x1 - .5)
    a.set_ylim(y1 - .5, y0 - .5)


def outline(a, origin, colour, label, style="-"):
    club = club3d.Club(SCENE, origin, *s["sole"], shape)
    m, _axis, _lead = club.mask(*ANGLES, 0.0, 0.0, SIZE)
    b = club3d.boundary(m)
    a.plot(*np.vstack([b, b[:1]]).T, color=colour, linewidth=1.5, linestyle=style, label=label)


T = info["TRACE"]
best = table[("TRACE", NAMES[1])]
base_image(axs[0], s["frames"][43])
outline(axs[0], T["o43"], "#39ff14", "model on f43 (fitted to the owner's trace)")
axs[0].plot(*np.vstack([trace, trace[:1]]).T, color="#ff2020", linewidth=1.0, label="owner's trace")
axs[0].set_title("frame 43, %.2f ms before contact" % (t_c - times[43]), fontsize=10)
base_image(axs[1], s["frames"][44])
outline(axs[1], T["o44e"], "#39ff14", "model on f44 from shaft line + head top edge")
outline(axs[1], T["o44f"], "#00e5ff", "model on f44 from the shaft alone (line + ferrule)", style="--")
e = T["edge44"]
axs[1].scatter(e[:, 0], e[:, 1], s=16, c="#ff2bd6", marker="_", linewidths=1.2, label="top edge found on f44")
axs[1].set_title("frame 44, %.2f ms after contact (ball in flight above the head)" % (times[44] - t_c), fontsize=10)
base_image(axs[2], s["frames"][43])
outline(axs[2], best[3], "#ffe600", "model at the contact instant")
axs[2].add_patch(plt.Circle(ball_px, r_px, fill=False, color="w", linewidth=0.9, linestyle=":"))
cp = SCENE.project(SCENE.ball - geom.BALL_RADIUS_MM * geom.axes(*ANGLES)[2])[0]
axs[2].scatter([cp[0]], [cp[1]], s=70, marker="x", c="#ff2020", linewidths=1.6, label="ball's contact point")
axs[2].set_title("at contact, drawn over frame 43's pixels with the resting ball", fontsize=10)
for a in axs[:3]:
    a.legend(fontsize=7, loc="lower left")
a = axs[3]
uv = geom.OUTLINE * SIZE
a.fill(-uv[:, 0], uv[:, 1], color="#d9dde3", zorder=0)
a.plot(-np.append(uv[:, 0], uv[0, 0]), np.append(uv[:, 1], uv[0, 1]), color="#555", linewidth=1.0)
cols = dict(zip(NAMES, ("#1f77b4", "#d62728", "#2ca02c", "#9467bd")))
for (anchor, name), (u, v, _g, _o) in table.items():
    a.scatter([-u], [v], s=70, marker="s" if anchor == "TRACE" else "o", facecolors=cols[name] if anchor == "TRACE" else "none", edgecolors=cols[name], linewidths=1.5,
              label="%s anchor, %s" % ("trace" if anchor == "TRACE" else "automatic", name))
a.scatter([-pl["impact_offset_mm"]], [pl["impact_height_mm"]], s=90, marker="x", c="k", label="application 0fa9870b")
a.axhline(0, color="#999", linewidth=0.5)
a.axvline(0, color="#999", linewidth=0.5)
a.set_aspect("equal")
a.set_xlabel("face-on, toe LEFT; mm from the face centre")
a.set_ylabel("mm up from the face centre")
a.set_title("shot 1 impact point on the generic face at size %.2f (research reading)" % SIZE, fontsize=10)
a.legend(fontsize=6, loc="upper right")
fig.tight_layout()
fig.savefig(AUDIT / "figures" / "impact-shot1.png", dpi=58)
