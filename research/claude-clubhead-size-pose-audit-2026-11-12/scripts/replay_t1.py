"""PROTOCOL addendum 14: the application's own raw replay route, unmodified, on shots of the EARLIER session
(pi-handoff/Harjot-outdoor-test-1, arm5), whose face photos this audit has never opened. Only the replay record is
kept (results/t1-shot<N>/replay.json); nothing in the session folder is written and no face photo is read.
Usage: replay_t1.py <shot>"""
import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path

from scripts.analysis import replay_raw_fusion as raw

from common import AUDIT

APP = Path("F:/Coding/OpenFlight/openflight-integration")
T1 = Path("F:/Coding/OpenFlight/pi-handoff/Harjot-outdoor-test-1/arm5/paired/run-01")
shot = int(sys.argv[1])
out = AUDIT / "results" / ("t1-shot%d" % shot)
out.mkdir(parents=True, exist_ok=True)
args = [str(T1 / "session_20261024_171945_arm5.jsonl"), str(shot), "--camera", "--iwr", "--full-shot", "--timing-coverage", "--output", str(out / "replay.json")]
head = subprocess.check_output(["git", "-C", str(APP), "rev-parse", "HEAD"], text=True).strip()
started = time.perf_counter()
error, code = None, None
try:
    with (out / "stdout.txt").open("w") as so, (out / "stderr.txt").open("w") as se:
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            code = raw.main(args)
except Exception as exc:  # recorded, not hidden
    error = {"type": type(exc).__name__, "message": str(exc)}
(out / "RUN.json").write_text(json.dumps({"application_head": head, "args": args, "returncode": code, "exception": error, "elapsed_s": round(time.perf_counter() - started, 2)}, indent=1))
print("t1 shot", shot, "return", code, "error", error, "| replay.json written:", (out / "replay.json").exists())
