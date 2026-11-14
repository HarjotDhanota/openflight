import json
d = json.load(open("F:/Coding/OpenFlight/openflight-integration/src/openflight/camera/club_outline_reference.json"))
def show(o, depth=0):
    for k, v in o.items():
        if isinstance(v, dict):
            print("  " * depth + k + ":"); show(v, depth + 1)
        elif isinstance(v, list) and len(v) > 6:
            print("  " * depth + "%s: list[%d] first=%s" % (k, len(v), json.dumps(v[:2])[:120]))
        else:
            print("  " * depth + "%s: %s" % (k, json.dumps(v)[:600]))
show(d)
