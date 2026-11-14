"""PROTOCOL addendum 9, T4: the OPS spin records of shots 1-4 as the unmodified replay wrote them. Shots 1 and 4
report the identical spin; is the value a whole number of spectral bins, and do the records differ otherwise?
Read-only. Writes results/SPIN-CHECK.txt. Usage: spin_check.py"""
import json

from common import AUDIT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


def short(v, n=700):
    s = json.dumps(v)
    return s if len(s) <= n else s[:n] + " ...[%d chars]" % len(s)


values = {}
for n in (1, 2, 3, 4):
    path = AUDIT / "results" / ("shot%d" % n) / "replay.json"
    if not path.exists():
        say("shot %d: no replay in this audit" % n)
        continue
    ops = json.loads(path.read_text())["stages"]["ops"]
    res = ops.get("result") or {}
    spin = res.get("spin")
    say("SHOT %d  ops status %s; ball %.2f mph, club %s mph" % (n, ops.get("status"), res.get("ball_speed_mph") or float("nan"), res.get("club_speed_mph")))
    if not isinstance(spin, dict):
        say("   spin record: %s" % short(spin))
        continue
    for k, v in spin.items():
        say("   %-22s %s" % (k, short(v, 300)))
    rpm = spin.get("spin_rpm")
    if isinstance(rpm, (int, float)):
        values[n] = rpm
        hz = rpm / 60.0
        for size in (128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768):
            for rate in (30000.0,):
                bins = hz / (rate / size)
                if abs(bins - round(bins)) < 1e-6:
                    say("   -> %.6f rpm = %.6f Hz = exactly %d bins of %g Hz / %d" % (rpm, hz, round(bins), rate, size))
    say()
same = [(a, b) for a in values for b in values if a < b and values[a] == values[b]]
say("identical spin values: %s" % (", ".join("shots %d and %d (%.6f rpm)" % (a, b, values[a]) for a, b in same) or "none"))
(AUDIT / "results" / "SPIN-CHECK.txt").write_text("\n".join(OUT) + "\n")
