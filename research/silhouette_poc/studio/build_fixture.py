"""Regenerate the small committed first-load Studio session."""

from __future__ import annotations

import json
from pathlib import Path

from silhouette_poc.studio.session import build_fixture


def main() -> None:
    target = Path(__file__).resolve().parent / "fixtures" / "fixture_session.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_fixture(), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(target)


if __name__ == "__main__":
    main()
