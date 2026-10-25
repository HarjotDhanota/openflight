"""Decode saved ``*.partial.l3dump`` files and report what each one still holds.

A failed IWR6843 transfer is saved beside the session's dumps as
``iwr6843_<time>_<seq>.partial.l3dump``. This runs ``partial_dump.recover_partial_dump``
on each and prints one JSON line per file: the runs it located, the frames it kept and
dropped, and why it stopped if it did. With ``--write`` the kept frames are written as
``<name>.recovered.partial.l3dump``: still a partial file by name, so no replay, upload or
pipeline step ever takes it for a whole capture.

    uv run python scripts/iwr6843/recover_partial_dump.py <session dir>/**/*.partial.l3dump
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from openflight.iwr6843.dump import PARTIAL_DUMP_SUFFIX  # noqa: E402
from openflight.iwr6843.partial_dump import recover_partial_dump  # noqa: E402

RECOVERED_SUFFIX = ".recovered" + PARTIAL_DUMP_SUFFIX


def recovered_path(path: Path) -> Path:
    """Where ``--write`` puts a file's kept frames."""
    name = path.name
    stem = name[: -len(PARTIAL_DUMP_SUFFIX)] if name.endswith(PARTIAL_DUMP_SUFFIX) else path.stem
    return path.with_name(stem + RECOVERED_SUFFIX)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--write", action="store_true", help="write each file's kept frames")
    args = parser.parse_args(argv)
    for path in args.paths:
        if path.name.endswith(RECOVERED_SUFFIX):
            continue
        recovery = recover_partial_dump(path.read_bytes())
        record = {"path": str(path), **recovery.to_dict()}
        if args.write and recovery.usable:
            target = recovered_path(path)
            target.write_bytes(recovery.raw)
            record["written"] = str(target)
        print(json.dumps(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
