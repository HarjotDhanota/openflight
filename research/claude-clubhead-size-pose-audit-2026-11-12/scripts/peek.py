import json, sys
p = "F:/Coding/OpenFlight/research/shot1-metric-orientation-2026-11-12/current-replay/"
s = json.load(open(p + "placement.json"))
def keys(o, depth=0, maxd=2):
    if isinstance(o, dict) and depth < maxd:
        for k, v in o.items():
            t = type(v).__name__
            brief = json.dumps(v)[:110] if not isinstance(v, (dict, list)) else ("%s[%d]" % (t, len(v)))
            print("  " * depth + "%s: %s" % (k, brief))
            keys(v, depth + 1, maxd)
keys(s, 0, 2)
d = s["placement"]["diagnostics"]
for k in ("club_model", "shaft_constraint", "club_path", "attack_angle", "club_speed_mps", "bracket_frames", "ball_range_mm", "camera_tilt", "normal_speed", "time_base", "frames"):
    if k in d: print(k, json.dumps(d[k])[:900])
print(json.dumps(s.get("orientation"))[:1200])
print(json.dumps(s.get("frames"))[:1500])
