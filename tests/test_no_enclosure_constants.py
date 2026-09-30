"""No code path outside the rig file supplies enclosure geometry (wiring audit C1).

The July and August enclosures live on as literals: the 8.25 in camera height,
the 0.152/0.1524 m radar height and the 64 mm tee offset. Any of them in src/
means a code path that can hand the swing server a geometry the rig file never
stated.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
OLD_ENCLOSURE_CONSTANTS = {0.20955, 0.152, 0.1524, 0.064}


def _numeric_literals(path: Path):
    text = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.NUMBER:
            continue
        try:
            value = float(token.string.replace("_", ""))
        except ValueError:
            continue
        yield token.start[0], value


def test_no_old_enclosure_constant_is_left_in_src():
    found = [
        f"{path.relative_to(SRC)}:{line} {value}"
        for path in sorted(SRC.rglob("*.py"))
        for line, value in _numeric_literals(path)
        if value in OLD_ENCLOSURE_CONSTANTS
    ]
    assert found == []
