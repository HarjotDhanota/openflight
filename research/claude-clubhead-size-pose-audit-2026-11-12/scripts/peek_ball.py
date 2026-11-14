"""Print what the shot record says about the ball (speed, launch, timing) for shot 1. Read-only."""
import json

from common import AUDIT

folder = AUDIT / "results" / "shot1"
state = json.loads((folder / "placement.json").read_text())
replay = json.loads((folder / "replay.json").read_text())


def walk(o, path="", depth=0):
    if isinstance(o, dict):
        for k, v in o.items():
            p = path + "/" + str(k)
            if isinstance(v, (dict, list)):
                if depth < 3:
                    walk(v, p, depth + 1)
            elif any(w in str(k).lower() for w in ("ball_speed", "launch", "azimuth", "club_speed", "smash", "contact", "direction", "spin")):
                print(p, "=", json.dumps(v)[:160])
    elif isinstance(o, list) and o and isinstance(o[0], dict) and depth < 3:
        walk(o[0], path + "[0]", depth + 1)


print("--- placement.json"); walk(state)
print("--- replay.json keys:", list(replay.keys())[:40])
walk(replay)
prep = state["prepared"]
print("contact block:", json.dumps(prep["contact"])[:900])
print("frame times 42..46:", [round(prep["frame_time_ms"][f] - prep["contact_ms"], 3) for f in range(42, 47)])
cm = state["collision_model"]
print("collision inputs:", json.dumps(cm["inputs"])[:1200])
print("collision metrics:", json.dumps(cm["metrics"])[:900])
