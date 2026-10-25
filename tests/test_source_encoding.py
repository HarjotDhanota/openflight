"""Source text is UTF-8 once, never decoded as cp1252 and re-encoded ("Â°" for "°")."""

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "openflight"
# UTF-8 text read as cp1252 and saved again: "Â°", "Â±", "â€”", "â†’", "â†\x90", "Ã©" ...
DOUBLE_ENCODED = re.compile("Â[\u00a0-\u00bf]|â€.|â†.|Ã[\u0080-\u00bf]")


def test_no_source_file_holds_double_encoded_text():
    offenders = {
        str(path.relative_to(SOURCE)): sorted(set(DOUBLE_ENCODED.findall(text)))
        for path in SOURCE.rglob("*.py")
        if DOUBLE_ENCODED.search(text := path.read_text(encoding="utf-8"))
    }
    assert offenders == {}
