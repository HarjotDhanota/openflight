"""Hash every original input this audit reads. Usage: hash_inputs.py <label>  (writes results/INPUT-HASHES-<label>.json)."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path("F:/Coding/OpenFlight/research/fusion-timing-replay-2026-11-05/run-002/package/sessions/harjot-outdoor-test-2/arm6/paired/run-01")
OUT = Path(__file__).resolve().parents[1] / "results"
files = sorted(p for p in ROOT.rglob("*") if p.is_file() and (
    "camera_2026" in p.as_posix() or "face-photos" in p.as_posix() or p.suffix in (".jsonl", ".json")))
table = {}
for p in files:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    table[p.relative_to(ROOT).as_posix()] = {"sha256": h.hexdigest(), "bytes": p.stat().st_size}
(OUT / ("INPUT-HASHES-%s.json" % sys.argv[1])).write_text(json.dumps({"root": ROOT.as_posix(), "files": table}, indent=1))
print(len(table), "files hashed")
for k, v in table.items():
    print(v["sha256"][:16], "%10d" % v["bytes"], k)
