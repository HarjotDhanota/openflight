"""Build a small synthetic tester session and its real bundle for the review page tests.

    uv run python ui/tests/e2e/fixtures/make_session_bundle.py <empty folder>

Prints JSON naming the bundle. Nothing here is recorded evidence.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

from scripts.analysis import analyze_tester_session as runner  # noqa: E402
from tests.session_fixtures import TESTER, capture_tree  # noqa: E402

root = capture_tree(Path(sys.argv[1]))
viewer = REPO / "ui" / "public" / "session-review.html"
if runner.analyze(root, TESTER, package=True, viewer=viewer) != 0:
    raise SystemExit("analysis failed")
job = json.loads((root / TESTER / "analysis" / "job.json").read_text(encoding="utf-8"))
print(json.dumps({"bundle": job["bundle"]["path"], "tester_id": TESTER}))
