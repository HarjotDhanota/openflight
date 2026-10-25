"""Serve the tester's read-only session review for local copies of tester sessions."""

import re
import sys
from pathlib import Path

from flask import Flask

from openflight.camera import session_review_routes as review_routes

REPO = Path(sys.argv[2])
app = Flask(__name__)
review_routes.register_session_review(
    app,
    sessions_root=Path(sys.argv[1]),
    valid_tester=lambda value: bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value)),
    page_path=REPO / "ui" / "public" / "session-review.html",
)
app.run(host="127.0.0.1", port=8091)
