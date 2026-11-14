"""A Maltby TS4 7-iron model for RESEARCH comparison only (never a product constant).

Shape: the face photo of the owner's own club (shot 1), rectified with its score lines (photo_grooves.py): the lines'
convergence and spacing give the face plane's tilt to the camera, so the outline comes out in true proportion.
Scale and the parts a face photo cannot give: the maker's published numbers (GolfWorks spec sheet, supplied by the owner):
   7-iron: loft 33, lie 62.5, offset 0.100 in, bounce 4.
   Details table (it is the 6-iron's on Maltby's other pages; taken as is): toe height 2.190 in, heel height 1.108 in,
   blade length 3.153 in, hosel length 2.156 in, hosel OD 0.531 in, sole width 0.765 in.
ONE spec number sets the scale: toe height 55.6 mm = the photo's leading-edge-to-toe-peak height across the score lines
(mean of the inner and outer outline conventions). Everything else from the spec is then a CHECK or a 3-D part.
Writes results/ts4-model.json, results/TS4-MODEL.txt, figures/ts4-model.png. Usage: ts4_model.py"""
import json
import math

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import geom
import photo_landmarks as pl
from common import AUDIT, ROOT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


IN = 25.4
SPEC = {"loft": 33.0, "lie": 62.5, "offset": 0.100 * IN, "bounce": 4.0, "toe_height": 2.190 * IN, "heel_height": 1.108 * IN, "blade_length": 3.153 * IN,
        "hosel_length": 2.156 * IN, "hosel_od": 0.531 * IN, "sole_width": 0.765 * IN}
SHOT = 1
img = cv2.imread(str(ROOT / "face-photos" / ("shot-%04d.pgm" % SHOT)), cv2.IMREAD_GRAYSCALE).astype(np.float32)
smooth = cv2.GaussianBlur(img, (0, 0), 1.0)
mag = np.hypot(cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3))
g = json.loads((AUDIT / "results" / ("photo-grooves-shot%d.json" % SHOT)).read_text())
F, PP = 1000.0, np.array([319.5, 199.5])      # nominal camera, as the application uses it


def snap(landmarks, reach):
    pts = np.asarray(landmarks, dtype=float)
    out = []
    for i, p in enumerate(pts):
        t = pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)]
        n = np.array([-t[1], t[0]]) / np.linalg.norm(t)
        cand = [p + s * n for s in np.arange(-reach, reach + 0.01, 0.5)]
        out.append(cand[int(np.argmax([mag[int(round(c[1])), int(round(c[0]))] for c in cand]))])
    return np.asarray(out)


def ray(px):
    px = np.atleast_2d(px)
    r = np.column_stack([(px[:, 0] - PP[0]) / F, (px[:, 1] - PP[1]) / F, np.ones(len(px))])
    return r / np.linalg.norm(r, axis=1)[:, None]


# ---- the face plane from the score lines
ends = np.array([[l["toe_end"], l["heel_end"]] for l in g["lines"]])
centre_px = ends.reshape(-1, 2).mean(axis=0)
a = math.radians(-g["angle_deg"])                    # image direction of the lines (toward the heel, image right)
u_img = np.array([math.cos(a), math.sin(a)])
if u_img[0] < 0:
    u_img = -u_img
d_u = ray(np.array(g["vp_u"]))[0]
if d_u[:2] @ u_img < 0:
    d_u = -d_u
t_img = np.array([u_img[1], -u_img[0]])               # across the lines, up the face in the image
if t_img[1] > 0:
    t_img = -t_img
if g["vp_t"] is not None and abs(g["c"]) > 2.0 * g["c_sigma"]:
    d_t = ray(np.array(g["vp_t"]))[0]
    if d_t[:2] @ t_img < 0:
        d_t = -d_t
    across_note = "spacing shows perspective across the lines (c = %.5f)" % g["c"]
else:
    d_t = np.array([t_img[0], t_img[1], 0.0])
    across_note = "spacing shows no measurable perspective across the lines (c = %.5f +- %.5f): taken as none" % (g["c"], g["c_sigma"])
r0 = ray(centre_px)[0]
normal = np.cross(d_u, d_t)
normal /= np.linalg.norm(normal)
if normal @ r0 > 0:
    normal = -normal                     # the face looks at the camera: its outward normal (toward the target) points back at the lens
e_v = np.cross(-d_u, normal)             # toe x normal = up, the same handedness as the rear-view model
assert e_v[:2] @ t_img > 0
tilt_u = math.degrees(math.asin(abs(d_u[2])))
say("score lines: %d fitted, direction %.1f deg, spacing %.2f px; they run at %.1f deg to the image plane (the %s end is nearer the camera); %s" % (
    len(g["lines"]), g["angle_deg"], g["pitch_px"], tilt_u, "toe" if d_u[2] > 0 else "heel", across_note))


def rectify(px):
    """Photo pixels -> coordinates in the face plane (arbitrary units): along the score lines toward the TOE, and up."""
    r = ray(px)
    X = r * (float(normal @ r0) / (r @ normal))[:, None]      # plane through the centre ray at unit distance
    return np.column_stack([-(X @ d_u), X @ e_v])


outer_px, inner_px = snap(pl.OUTER[SHOT], 4.0), snap(pl.INNER[SHOT], 2.0)
outer, inner = rectify(outer_px), rectify(inner_px)
h_outer, h_inner = float(np.ptp(outer[:, 1])), float(np.ptp(inner[:, 1]))
scale = SPEC["toe_height"] / (0.5 * (h_outer + h_inner))        # mm per unit
outer, inner = outer * scale, inner * scale
say("scale: toe height %.1f mm set equal to the mean of the outer and inner outline heights, which are then %.1f and %.1f mm; the face is %.0f mm from the lens, %.2f px per mm" % (
    SPEC["toe_height"], h_outer * scale, h_inner * scale, scale, F / scale))


def dense(poly, n=12):
    return np.vstack([np.linspace(poly[i], poly[i + 1], n, endpoint=False) for i in range(len(poly) - 1)] + [poly[-1:]])


# the model outline: midway between the two conventions (outer points pulled half-way to the nearest inner point)
inner_d = dense(inner)
mid = np.array([0.5 * (p + inner_d[int(np.argmin(np.linalg.norm(inner_d - p, axis=1)))]) for p in outer])
# datum: toe extreme and leading-edge tangent
toe_u = float(mid[:, 0].max())
le_v = float(mid[:, 1].min())
lines_mm = np.array([[rectify(np.array(l["toe_end"]))[0] * scale, rectify(np.array(l["heel_end"]))[0] * scale] for l in g["lines"]])
lengths = np.linalg.norm(lines_mm[:, 0] - lines_mm[:, 1], axis=1)
longest = int(np.argmax(lengths))
pitch_mm = float(np.median(np.abs(np.diff(lines_mm[:, :, 1].mean(axis=1)))))
centre_u = float(lines_mm[longest, :, 0].mean())
inner_dd = dense(inner, 20)
col = inner_dd[np.abs(inner_dd[:, 0] - centre_u) < 1.5]
face_bottom, face_top = float(col[:, 1].min()), float(col[:, 1].max())
centre_v = 0.5 * (face_bottom + face_top)
say("score lines in mm: spacing %.2f, longest %.1f (line %d of %d from the top), its toe end %.1f mm from the toe extreme" % (pitch_mm, lengths[longest], longest + 1, len(lengths), toe_u - float(lines_mm[longest, :, 0].max())))
say("face centre by the rule (middle of the longest line; midway between face bottom and top line there): %.1f mm from the toe extreme, %.1f mm above the leading edge (generic drawing: 44.2, 24.6)" % (toe_u - centre_u, centre_v - le_v))

# ---- outline in face coordinates about the face centre; ordered heel-bottom -> sole -> toe -> top line -> heel-top
uv = np.column_stack([mid[:, 0] - centre_u, mid[:, 1] - centre_v])[::-1]
x_from_toe = (toe_u - centre_u) - uv[:, 0]
top = uv[(uv[:, 1] > 0) & (x_from_toe > 25)]
sl = np.polyfit((toe_u - centre_u) - top[:, 0], top[:, 1], 1)
say("outline: %d points; reaches %.1f mm from the toe along the top line and %.1f mm along the sole; toe peak %.1f mm above the leading edge, %.1f mm from the toe; top line falls %.1f deg toward the heel (generic 24.9)" % (
    len(uv), float(x_from_toe[uv[:, 1] > 0].max()), float(x_from_toe[uv[:, 1] < 0].max()), float(uv[:, 1].max() - (le_v - centre_v)), float(x_from_toe[int(np.argmax(uv[:, 1]))]), math.degrees(math.atan(-sl[0]))))
top_x = (toe_u - centre_u) - top[:, 0]
order = np.argsort(top_x)
top_x, top_h = top_x[order], (top[:, 1] - (le_v - centre_v))[order]
for station, generic in ((40.0, "51.4"), (60.0, "42.4"), (70.0, "37.3"), (80.0, "33.0")):
    say("   top line %.0f mm from the toe: %.1f mm above the leading edge (generic at size 1: %s)" % (station, float(np.interp(station, top_x, top_h)), generic))

# ---- hosel: where the shaft axis meets the ground line, from the photo's two hosel edges, done in 3-D
left, right = snap(pl.HOSEL_LEFT[SHOT], 3.0), snap(pl.HOSEL_RIGHT[SHOT], 3.0)
ys = np.linspace(max(left[:, 1].min(), right[:, 1].min()), min(left[:, 1].max(), right[:, 1].max()), 6)
axis_px = np.column_stack([0.5 * (np.interp(ys, left[:, 1], left[:, 0]) + np.interp(ys, right[:, 1], right[:, 0])), ys])
width_px = float(np.mean((np.interp(ys, right[:, 1], right[:, 0]) - np.interp(ys, left[:, 1], left[:, 0])))) * abs(math.cos(math.atan2(axis_px[-1, 0] - axis_px[0, 0], axis_px[-1, 1] - axis_px[0, 1])))
lo, li = math.radians(SPEC["loft"]), math.radians(SPEC["lie"])
# club frame: toe (u+), up the face (v+), face normal toward the target. Shaft direction and ground point in it.
shaft_tun = np.array([-math.cos(li), math.sin(li) * math.cos(lo), math.sin(li) * math.sin(lo)])
axis_behind_le = SPEC["hosel_od"] / 2.0 - SPEC["offset"]         # hosel's front is `offset` ahead of the leading edge


U0, V0 = rectify(centre_px)[0] * scale


def club_to_cam(u, v, w):
    """Club coordinates about the face centre (mm: toe, up the face, out of the face) -> the photo camera's coordinates (mm)."""
    return r0 * scale - (u + centre_u - U0) * d_u + (v + centre_v - V0) * e_v + w * normal


def project(X):
    X = np.atleast_2d(X)
    return np.column_stack([PP[0] + F * X[:, 0] / X[:, 2], PP[1] + F * X[:, 1] / X[:, 2]])




def axis_misfit(x_ground):
    ground_tun = np.array([(toe_u - centre_u) - x_ground, (le_v - centre_v) + axis_behind_le * math.sin(lo), -axis_behind_le * math.cos(lo)])
    pts = [ground_tun + s * shaft_tun for s in (25.0, 70.0)]
    cam = np.array([club_to_cam(*p) for p in pts])
    px = project(cam)
    d = px[1] - px[0]
    nrm = np.array([-d[1], d[0]]) / np.linalg.norm(d)
    return float(np.mean((axis_px - px[0]) @ nrm)), px


xs = np.linspace(55.0, 95.0, 401)
mis = np.array([axis_misfit(x)[0] for x in xs])
x_photo = float(xs[int(np.argmin(np.abs(mis)))])
# Maltby measures horizontal positions from where the hosel centreline meets the ground line; blade length is taken the same way
x_ground = SPEC["blade_length"]
_m, axis_model_px = axis_misfit(x_ground)
_m2, axis_photo_px = axis_misfit(x_photo)
say()
say("hosel in the photo: %.1f px across = %.1f mm at the face's scale (spec OD %.1f mm; the hosel stands nearer the lens than the face, so it should read a little large)" % (width_px, width_px * scale / F, SPEC["hosel_od"]))
say("shaft axis meets the ground line: USED %.1f mm from the toe extreme = the spec blade length, read as toe to the hosel centreline at the ground (Maltby's stated datum for its other horizontal measures)" % x_ground)
say("   check from the photo's hosel edges, in 3-D with the spec lie and loft: %.1f mm (the model axis at %.1f misses the photographed axis by %.1f px); generic drawing 78.2" % (x_photo, x_ground, abs(_m)))
say("   heel height by the spec %.1f mm; this model's top line is that high %.1f mm from the toe (its last traced point is %.1f mm from the toe at %.1f mm)" % (SPEC["heel_height"], float(np.interp(-SPEC["heel_height"], -top_h, top_x)), float(top_x[-1]), float(top_h[-1])))

model = {"name": "ts4-7-iron (research)", "outline_uv": uv.round(3).tolist(), "toe_u": toe_u - centre_u, "le_v": le_v - centre_v, "static_loft": SPEC["loft"], "static_lie": SPEC["lie"],
         "axis_ground_from_toe": x_ground, "axis_behind_le": axis_behind_le, "hosel_radius": SPEC["hosel_od"] / 2.0, "hosel_length": SPEC["hosel_length"],
         "shaft_radius": 4.7, "sole_width": SPEC["sole_width"], "bounce": SPEC["bounce"], "score_line_pitch": pitch_mm, "longest_line": float(lengths[longest]),
         "source": "shot 1 face photo rectified by its score lines; scale from the published toe height; hosel from published lie, loft, offset, OD"}
(AUDIT / "results" / "ts4-model.json").write_text(json.dumps(model, indent=1))
(AUDIT / "results" / "TS4-MODEL.txt").write_text("\n".join(OUT) + "\n")

fig, axs = plt.subplots(1, 2, figsize=(22, 9))
a0 = axs[0]
lo_, hi_ = np.percentile(img, [1, 99.5])
a0.imshow(np.clip((img - lo_) / (hi_ - lo_), 0, 1), cmap="gray", interpolation="nearest")
a0.plot(outer_px[:, 0], outer_px[:, 1], color="#00e5ff", linewidth=0.9, label="outer outline (snapped landmarks)")
a0.plot(inner_px[:, 0], inner_px[:, 1], color="#ffe600", linewidth=0.9, label="inner outline")
for l in g["lines"]:
    a0.plot([l["toe_end"][0], l["heel_end"][0]], [l["toe_end"][1], l["heel_end"][1]], color="#39ff14", linewidth=0.6)
a0.plot(left[:, 0], left[:, 1], color="#ff2bd6", linewidth=1.0)
a0.plot(right[:, 0], right[:, 1], color="#ff2bd6", linewidth=1.0, label="hosel edges")
a0.plot(axis_model_px[:, 0], axis_model_px[:, 1], color="#ff8c00", linewidth=1.2, linestyle="--", label="model shaft axis, projected in 3-D")
a0.set_xlim(90, 500)
a0.set_ylim(320, 20)
a0.legend(fontsize=8, loc="lower right")
a0.set_title("shot 1 face photo: what the TS4 model is built from", fontsize=10)
a1 = axs[1]
gen = geom.OUTLINE
a1.plot(-np.append(gen[:, 0], gen[0, 0]), np.append(gen[:, 1], gen[0, 1]), color="#999", linewidth=1.0, label="generic drawing, size 1.00")
a1.plot(-np.append(gen[:, 0], gen[0, 0]) * 0.93, np.append(gen[:, 1], gen[0, 1]) * 0.93, color="#999", linewidth=1.0, linestyle=":", label="generic, size 0.93 (fitted earlier)")
a1.plot(-uv[:, 0], uv[:, 1], color="#c2267f", linewidth=1.6, label="TS4 from the photo and the spec sheet")
for k in range(len(lines_mm)):
    a1.plot(-(lines_mm[k, :, 0] - centre_u), lines_mm[k, :, 1] - centre_v, color="#c2267f", linewidth=0.5)
gx = -((toe_u - centre_u) - x_ground)
a1.plot([gx, gx - 60 * math.cos(math.atan(math.tan(li) * math.cos(lo))) * -1], [le_v - centre_v, le_v - centre_v + 60 * math.sin(math.atan(math.tan(li) * math.cos(lo)))], color="#ff8c00", linestyle="--", linewidth=1.0, label="TS4 shaft axis (face-on view)")
a1.axhline(0, color="#ccc", linewidth=0.5)
a1.axvline(0, color="#ccc", linewidth=0.5)
a1.set_aspect("equal")
a1.set_xlabel("face-on, toe LEFT, mm from each model's own face centre")
a1.legend(fontsize=8, loc="upper right")
a1.set_title("the TS4 outline against the generic drawing", fontsize=10)
fig.tight_layout()
fig.savefig(AUDIT / "figures" / "ts4-model.png", dpi=60)
