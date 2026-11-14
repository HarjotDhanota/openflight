import json
d = json.load(open("F:/Coding/OpenFlight/openflight-integration/src/openflight/camera/face_centre_reference.json"))
for k in ("iron", "wedge"):
    print(k, json.dumps(d["categories"][k], indent=1))
