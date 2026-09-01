"""
OpenFlight enclosure — body shell v0 (massing + locked features).
Reads params.py. Exports STEP (assembly) + STL per printed part.

v0 scope: outer form, 3mm shell, print split @165mm with gasket flange+groove,
R50-style handle slot, 4x M8 feet bosses, rear port holes, placeholder
display/radar openings (real geometry lands after caliper session).

Run:  python body_shell.py
"""

from build123d import *
import params as P

# --- derived ---
W, D, H = P.ENV_W, P.ENV_D, P.ENV_H
SPLIT = P.SPLIT_LOWER_H
FLANGE_W = 10.0          # internal seam flange (gasket groove needs > wall)
FLANGE_T = 6.0
TOP_R = 10.0             # top edge pillowing

# ============ outer form ============
outer = Box(W, D, H, align=(Align.CENTER, Align.CENTER, Align.MIN))
outer = fillet(outer.edges().filter_by(Axis.Z), radius=P.CORNER_R)
outer = fillet(outer.edges().group_by(Axis.Z)[-1], radius=TOP_R)

# ============ hollow ============
inner = offset(outer, amount=-P.WALL_T)
body = outer - inner

# ============ seam flange (both halves get one) ============
def ring(w, d, r, t, z):
    """rounded-rect ring plate: outer minus opening"""
    o = extrude(Plane.XY.offset(z) * RectangleRounded(w, d, r), amount=t)
    i = extrude(
        Plane.XY.offset(z) * RectangleRounded(w - 2 * FLANGE_W, d - 2 * FLANGE_W,
                                              max(r - FLANGE_W, 2)), amount=t)
    return o - i

flange = ring(W - 2 * P.WALL_T + 2, D - 2 * P.WALL_T + 2, P.CORNER_R - P.WALL_T,
              FLANGE_T, SPLIT - FLANGE_T / 2)
body = body + (flange & offset(outer, amount=-0.01))  # keep flange inside form

# ============ gasket groove (cut into lower flange top) ============
gc_w = W - 2 * P.WALL_T - FLANGE_W          # groove centerline rect
gc_d = D - 2 * P.WALL_T - FLANGE_W
groove = ring(gc_w + P.GASKET_GROOVE_W, gc_d + P.GASKET_GROOVE_W,
              max(P.CORNER_R - P.WALL_T - FLANGE_W / 2, 2),
              P.GASKET_GROOVE_DEPTH, SPLIT - P.GASKET_GROOVE_DEPTH)
# thin the ring to groove width
groove_inner = ring(gc_w - P.GASKET_GROOVE_W, gc_d - P.GASKET_GROOVE_W,
                    max(P.CORNER_R - P.WALL_T - FLANGE_W / 2 - P.GASKET_GROOVE_W, 1),
                    P.GASKET_GROOVE_DEPTH, SPLIT - P.GASKET_GROOVE_DEPTH)
groove = groove - groove_inner

# ============ handle slot (through-depth, upper rear shoulder) ============
slot_z = H - 22.0 - P.HANDLE_SLOT_H / 2
handle = extrude(
    Plane.XZ.offset(-D) * Pos(0, slot_z) *
    RectangleRounded(P.HANDLE_SLOT_W, P.HANDLE_SLOT_H, P.HANDLE_SLOT_H / 2 - 1),
    amount=2 * D,
)

# ============ feet bosses + insert pilots ============
feet_pos = [(sx * (W / 2 - 30), sy * (D / 2 - 30)) for sx in (-1, 1) for sy in (-1, 1)]
bosses = [Pos(x, y, 0) * Cylinder(P.FOOT_BOSS_OD / 2, 16,
          align=(Align.CENTER, Align.CENTER, Align.MIN)) for x, y in feet_pos]
pilots = [Pos(x, y, 0) * Cylinder(10.2 / 2, 14,     # VERIFY ruthex M8 pilot
          align=(Align.CENTER, Align.CENTER, Align.MIN)) for x, y in feet_pos]

# ============ rear ports (lower zone) ============
ports = []
port_z = 80.0
ports.append(Pos(-40, D / 2, port_z) * Rot(90, 0, 0) * Cylinder(P.USBC_PANEL_HOLE_D / 2, 30))
ports.append(Pos(35, D / 2, port_z) * Rot(90, 0, 0) *
             extrude(Rectangle(P.HDMI_PANEL_CUTOUT[0], P.HDMI_PANEL_CUTOUT[1]), 30, both=True))
ports.append(Pos(0, D / 2, port_z - 35) * Rot(90, 0, 0) * Cylinder(P.DC_JACK_HOLE_D / 2, 30))

# ============ placeholder openings (REAL GEOMETRY AFTER CALIPERS) ============
display_open = Pos(0, -D / 2, 195) * Rot(90, 0, 0) * \
    extrude(RectangleRounded(150, 100, 8), 30, both=True)   # TODO: 25deg inset
radar_open = Pos(0, -D / 2, 75) * Rot(90, 0, 0) * \
    extrude(RectangleRounded(150, 80, 8), 30, both=True)    # TODO: window plate

# ============ assemble & split ============
body = body + [b & outer for b in bosses]
body = body - groove - handle - pilots
for p in ports:
    body = body - p
body = body - display_open - radar_open

lower = split(body, bisect_by=Plane.XY.offset(SPLIT), keep=Keep.BOTTOM)
upper = split(body, bisect_by=Plane.XY.offset(SPLIT), keep=Keep.TOP)

# ============ export ============
export_step(Compound([lower, upper]), "enclosure_v0.step")
export_stl(lower, "body_lower_v0.stl")
export_stl(upper, "body_upper_v0.stl")
print("bbox lower:", lower.bounding_box())
print("bbox upper:", upper.bounding_box())
print("vol total (cm3):", round((lower.volume + upper.volume) / 1000, 1))
print("OK")
